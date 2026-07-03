Audit Report

## Title
No Time-Based Staleness Check on Chainlink Price Feeds Allows Stale Prices to Drive Incorrect rsETH Minting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards `updatedAt`, `roundId`, and `answeredInRound`, performing no heartbeat or round-completeness check. A stale Chainlink LST/ETH price — realistic during network congestion or an LST depeg event — is accepted without revert and propagates directly into rsETH minting calculations, allowing a depositor to receive more rsETH than the deposited asset is worth, diluting all existing rsETH holders.

## Finding Description
In `contracts/oracles/ChainlinkPriceOracle.sol` at line 52, `latestRoundData()` is called with all staleness-relevant return values discarded:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
``` [1](#0-0) 

No check of the form `block.timestamp - updatedAt > STALENESS_THRESHOLD` is performed, and no `answeredInRound < roundId` guard is applied. The returned `price` is immediately scaled and returned as the authoritative asset price.

This price flows into `LRTDepositPool.getRsETHAmountToMint()`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [2](#0-1) 

Which is called by `_beforeDeposit()`, invoked by the fully permissionless `depositAsset()`: [3](#0-2) 

The sibling contract `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol` demonstrates the protocol is aware of the required pattern — it applies both `answeredInRound < roundID` and `timestamp == 0` guards — but `ChainlinkPriceOracle` omits them entirely: [4](#0-3) 

**Exploit flow:**
1. Chainlink stETH/ETH feed goes stale (e.g., last updated >24h ago due to network congestion). Stale price: `1.01e18`. Actual market price: `0.99e18` (LST depeg in progress).
2. Attacker calls `LRTDepositPool.depositAsset(stETH, 100e18, 0, "")`.
3. `getRsETHAmountToMint` calls `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns `1.01e18` (no revert).
4. `rsethAmountToMint = (100e18 * 1.01e18) / rsETHPrice` — attacker receives ~2% more rsETH than the deposited stETH is actually worth.
5. Attacker redeems rsETH, extracting more ETH than deposited; the excess is drawn from existing rsETH holders' backing.

No admin action, no special role, and no oracle operator compromise is required. The feed going stale is a passive failure mode (network congestion, node failure) that has occurred historically.

## Impact Explanation
**High. Theft of unclaimed yield.** Existing rsETH holders' share of the backing pool is diluted when an attacker mints rsETH at an inflated stale price. Upon redemption, the attacker extracts more ETH than deposited, with the shortfall borne by existing holders. The same stale price fed into `_getTotalEthInProtocol()` / `_updateRsETHPrice()` can also cause `newRsETHPrice` to be computed incorrectly, potentially triggering the downside-protection pause and causing a temporary freeze of funds (Medium impact, secondary).

## Likelihood Explanation
Chainlink LST/ETH feeds (e.g., stETH/ETH, rETH/ETH) carry 24-hour heartbeats and 0.5% deviation thresholds. During periods of network congestion, oracle node failures, or rapid LST price movement (depeg), the feed can lag beyond the heartbeat. LST depegs are historically documented (stETH in 2022). The entry path (`depositAsset`) is fully permissionless, requires no special role, and is callable by any external account.

## Recommendation
Add both a round-completeness and a time-based staleness check in `ChainlinkPriceOracle.getAssetPrice()`, consistent with the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

if (answeredInRound < roundId) revert StalePrice();
if (updatedAt == 0) revert IncompleteRound();
if (price <= 0) revert InvalidPrice();
if (block.timestamp - updatedAt > STALENESS_THRESHOLD) revert StalePrice();
```

`STALENESS_THRESHOLD` should be set per feed based on its documented heartbeat (e.g., 24 hours + a small buffer for LST/ETH feeds).

## Proof of Concept

**Foundry fork test outline:**

```solidity
// Fork mainnet at a block where the stETH/ETH Chainlink feed is within heartbeat.
// Warp block.timestamp forward by 25 hours (beyond the 24h heartbeat).
// The feed's latestRoundData() still returns the last price (no revert from Chainlink).
// Call LRTDepositPool.depositAsset(stETH, 100e18, 0, "") as an unprivileged attacker.
// Assert rsethAmountToMint > expected amount based on actual current price.
// Redeem rsETH and assert ETH received > stETH deposited (in ETH terms at actual price).
```

Alternatively, a unit test can mock `AggregatorV3Interface.latestRoundData()` to return a price with `updatedAt = block.timestamp - 25 hours` and verify that `ChainlinkPriceOracle.getAssetPrice()` returns the stale price without reverting, while the corrected version reverts with `StalePrice`.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTDepositPool.sol (L99-118)
```text
    function depositAsset(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedERC20Token(asset)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L26-37)
```text
    function getRate() public view returns (uint256) {
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
    }
```
