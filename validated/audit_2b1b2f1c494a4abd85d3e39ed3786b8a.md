Audit Report

## Title
Missing Staleness Validation in `ChainlinkPriceOracle.getAssetPrice()` Enables Over-Minting of rsETH - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards `roundId`, `updatedAt`, and `answeredInRound`, performing no staleness, incomplete-round, or negative-price checks. A stale inflated price flows directly into `LRTDepositPool.getRsETHAmountToMint()`, causing a depositor to receive more rsETH than the fair value of their deposit and diluting all existing rsETH holders. The same codebase already contains a correct reference implementation in `ChainlinkOracleForRSETHPoolCollateral.sol`.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches the price as:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

All of `roundId`, `updatedAt`, and `answeredInRound` are silently discarded. There is no check that `answeredInRound >= roundId`, `updatedAt != 0`, or `price > 0`.

In contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` in the same repository correctly validates all three conditions:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

The unvalidated price propagates through `LRTOracle.getAssetPrice()`: [3](#0-2) 

...into `LRTDepositPool.getRsETHAmountToMint()`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [4](#0-3) 

The same stale price also feeds `LRTOracle._getTotalEthInProtocol()`, which computes the global `rsETHPrice` used as the denominator in the mint calculation: [5](#0-4) 

When the stale inflated price affects both numerator (`getAssetPrice(asset)`) and denominator (`rsETHPrice`, which is derived from the same oracle), the net effect depends on which assets are stale. If only one LST feed is stale and inflated, the numerator for that asset is inflated while the denominator reflects a mix of stale and fresh prices, resulting in net over-minting for the depositor of that specific asset.

## Impact Explanation
**High — Theft of unclaimed yield from existing rsETH holders.**

rsETH is a share token backed by total protocol TVL. Over-minting rsETH for a depositor using a stale inflated price means that depositor's shares represent more of the underlying TVL than they paid for. When the feed updates and `updateRSETHPrice()` is called, the true TVL is lower than the inflated mint assumed, so all pre-existing rsETH holders' shares are worth proportionally less. The attacker can then redeem via `LRTWithdrawalManager`, extracting value from existing holders. This matches the allowed impact: **High — Theft of unclaimed yield**.

## Likelihood Explanation
Chainlink feeds have heartbeat intervals (e.g., 24 hours for some LST/ETH feeds) and deviation thresholds. A feed can go stale during network congestion, sequencer downtime (on L2s), or when the price does not move enough to trigger an update. The protocol supports multiple LST assets (stETH, rETH, ETHx, swETH), each with its own feed; any single feed going stale is sufficient. No privileged role is required — any external caller can invoke `depositAsset()` or `depositETH()` during the stale window. [6](#0-5) 

## Recommendation
Apply the same staleness validation already present in `ChainlinkOracleForRSETHPoolCollateral.sol` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

require(price > 0, "Chainlink price <= 0");
require(updatedAt != 0, "Incomplete round");
require(answeredInRound >= roundId, "Stale price");
```

Additionally, add a per-feed configurable heartbeat check: `require(block.timestamp - updatedAt <= maxStaleness[asset], "Price too stale")`. [7](#0-6) 

## Proof of Concept

1. Deploy a fork of mainnet with a stETH/ETH Chainlink feed that has stopped updating (simulate by warping `block.timestamp` past the feed's heartbeat without a new round).
2. The last reported price is `1.05e18` (stETH at a 5% premium); the true current price has dropped to `1.00e18`.
3. Call `LRTDepositPool.depositAsset(stETH, 1000e18, 0, "")` from an unprivileged address.
4. `getRsETHAmountToMint` calls `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns stale `1.05e18` with no revert.
5. rsETH minted = `(1000e18 * 1.05e18) / rsETHPrice` — 5% more rsETH than the deposit is worth at current prices.
6. Call `LRTOracle.updateRSETHPrice()` after the feed resumes; the true TVL is lower than the inflated mint assumed, reducing the rsETH price for all existing holders.
7. Attacker initiates withdrawal via `LRTWithdrawalManager`, redeeming the inflated rsETH for more underlying assets than deposited, at the expense of existing holders.

A Foundry fork test can demonstrate this by: (a) recording `rsETHPrice` before the deposit, (b) mocking `latestRoundData` to return a stale inflated price, (c) executing the deposit, (d) calling `updateRSETHPrice`, and (e) asserting that the post-update `rsETHPrice` is lower than the pre-deposit value, confirming dilution.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L30-32)
```text
        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L336-344)
```text
        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

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
