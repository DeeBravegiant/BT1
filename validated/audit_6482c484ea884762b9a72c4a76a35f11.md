Audit Report

## Title
Missing Chainlink Staleness Check Allows Stale Price Acceptance for rsETH Minting — (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards the `updatedAt` timestamp and `answeredInRound` return values, performing no freshness validation. Any unprivileged depositor can call `LRTDepositPool.depositAsset()` during a Chainlink feed staleness window to receive rsETH computed at a stale inflated price, diluting existing rsETH holders. The same codebase already implements staleness checks in `ChainlinkOracleForRSETHPoolCollateral`, confirming developer awareness of the requirement.

## Finding Description
In `contracts/oracles/ChainlinkPriceOracle.sol` at line 52, `getAssetPrice()` destructures only the `price` field from `latestRoundData()`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
```

`updatedAt` (index 3) and `answeredInRound` (index 4) are discarded. No `block.timestamp - updatedAt` comparison exists anywhere in the contract. The raw price is normalized and returned directly at line 54.

This price flows into `LRTOracle.getAssetPrice()` → `LRTOracle._getTotalEthInProtocol()` (L339) → `LRTDepositPool.getRsETHAmountToMint()` (L520):

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`depositAsset()` is a public, permissionless function (L99). The exploit window: (1) Chainlink feed for a supported LST goes stale at price X while the real market price drops to Y < X; (2) `rsETHPrice` was last updated at price X; (3) attacker calls `depositAsset()` — `getAssetPrice()` returns stale X, ratio X/rsETHPrice is inflated, attacker receives excess rsETH; (4) when the oracle next updates to Y, `updateRSETHPrice()` recomputes a lower `rsETHPrice`, diluting all prior holders.

Existing guards are insufficient: the `pricePercentageLimit` check in `_updateRsETHPrice()` only fires when `updateRSETHPrice()` is explicitly called, not during individual deposits. The `minRSETHAmountExpected` slippage parameter protects the depositor, not existing holders.

Notably, `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol` (L30–32) in the same repository already implements `answeredInRound < roundID`, `timestamp == 0`, and `ethPrice <= 0` checks, confirming the developers know these validations are required but omitted them from `ChainlinkPriceOracle`.

## Impact Explanation
**High — Theft of unclaimed yield / dilution of existing rsETH holders.**

When a depositor exploits a stale inflated price, they receive more rsETH than the true underlying value warrants. When the oracle corrects, the rsETH/ETH exchange rate drops, transferring value from existing holders to the attacker. This is a concrete, quantifiable loss of yield/principal for existing rsETH holders, matching the "Theft of unclaimed yield" impact class. The secondary scenario (deflated stale price) maps to "Contract fails to deliver promised returns."

## Likelihood Explanation
Chainlink LST/ETH feeds (e.g., stETH/ETH, ETHx/ETH) have 24-hour heartbeats and 0.5–1% deviation thresholds. During low-volatility periods or elevated gas prices, feeds routinely remain stale for hours without triggering an update. No special privileges are required — any address can call `depositAsset()`. The attacker only needs to monitor the feed's `updatedAt` timestamp off-chain and submit a deposit transaction during the staleness window. This is repeatable across any supported LST feed.

## Recommendation
Add staleness validation in `ChainlinkPriceOracle.getAssetPrice()` using a per-asset configurable `maxStaleness` mapping:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

require(price > 0, "Invalid price");
require(answeredInRound >= roundId, "Stale round");
require(block.timestamp - updatedAt <= maxStaleness[asset], "Stale price");
```

Also add `updateMaxStalenessFor(address asset, uint256 maxStaleness_)` restricted to `onlyLRTManager`, consistent with the existing `updatePriceFeedFor` pattern.

## Proof of Concept
**Foundry fork test outline:**

1. Fork mainnet at a block where the stETH/ETH Chainlink feed (`0x86392dC19c0b719886221c78AB11eb8Cf5c52812`) has a recent `updatedAt`.
2. Warp `block.timestamp` forward by 25+ hours (beyond the 24h heartbeat) without triggering a feed update (use `vm.mockCall` to freeze `latestRoundData` at the stale high price while the "real" price has dropped 2%).
3. Record `rsETHPrice` and existing holder rsETH balance.
4. Call `LRTDepositPool.depositAsset(stETH, largeAmount, 0, "")` as the attacker.
5. Assert attacker received rsETH computed at the stale inflated price.
6. Call `updateRSETHPrice()` with the corrected oracle price.
7. Assert `rsETHPrice` decreased, confirming existing holders were diluted by the attacker's excess mint. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTOracle.sol (L336-343)
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

**File:** contracts/LRTDepositPool.sol (L515-521)
```text
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

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
