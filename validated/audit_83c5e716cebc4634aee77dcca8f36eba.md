Audit Report

## Title
Stale Chainlink Price Accepted Without Staleness or Validity Checks Enables rsETH Over-Minting — (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but binds only the `answer` field, silently discarding `updatedAt`, `answeredInRound`, and `roundId`. No staleness deadline, round-completeness check, or non-negative price guard is applied. The raw stale price flows directly into `LRTDepositPool.getRsETHAmountToMint()`, allowing any depositor to mint rsETH at an inflated rate during any Chainlink heartbeat window in which the feed has not yet reflected a real-world price drop, diluting all existing rsETH holders.

## Finding Description

`ChainlinkPriceOracle.getAssetPrice()` (lines 49–55) fetches the Chainlink round answer but discards every safety field:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

The five return values of `latestRoundData` are `(roundId, answer, startedAt, updatedAt, answeredInRound)`. Only `price` (second slot) is bound. Consequently:

- **No staleness check**: `updatedAt` is never compared against `block.timestamp - heartbeat`.
- **No round-completeness check**: `answeredInRound >= roundId` is never verified.
- **No non-negative guard**: `price` is cast directly to `uint256`; a zero or negative answer is not rejected.

This price is consumed in two critical paths:

**Path 1 — rsETH minting rate at deposit time:**

`LRTDepositPool.getRsETHAmountToMint()` divides the live Chainlink asset price by the stored `rsETHPrice`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [2](#0-1) 

**Path 2 — rsETH price update:**

`LRTOracle._getTotalEthInProtocol()` multiplies each asset's balance by its Chainlink price to compute total TVL, which then sets `rsETHPrice`:

```solidity
uint256 assetER = getAssetPrice(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
``` [3](#0-2) 

The `pricePercentageLimit` downside-protection mechanism in `_updateRsETHPrice()` only triggers when `updateRSETHPrice()` is called after the fact; it does not prevent the initial over-minting during the deposit. Furthermore, if `pricePercentageLimit` is zero (its default uninitialized value), no protection exists at all. [4](#0-3) 

## Impact Explanation

**Protocol insolvency / permanent dilution of rsETH holders (Critical).**

During any Chainlink heartbeat window in which the feed has not yet reflected a real-world price drop (e.g., an LST depeg), a depositor receives rsETH priced against the stale inflated rate. When the feed eventually corrects and `updateRSETHPrice()` is called, the computed TVL drops, `rsETHPrice` falls, and all pre-existing rsETH holders are diluted by the over-issued supply. Repeated across multiple depositors or a single large deposit within the staleness window, this constitutes systematic bad debt and structural insolvency. The attacker retains the excess rsETH regardless of whether the protocol subsequently pauses.

## Likelihood Explanation

Chainlink feeds have documented heartbeat windows (e.g., 24 h for stETH/ETH on mainnet). During high-volatility events — LST depegs, sequencer outages on L2 — the feed can lag real market prices by hours. This is a known, recurring condition, not a theoretical edge case. The exploit is permissionless: any caller can invoke the public `depositAsset()` or `depositETH()` entry points with no special privileges, no victim interaction, and no governance capture required. [5](#0-4) 

## Recommendation

Add staleness, round-completeness, and non-negative guards to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
mapping(address asset => uint256 stalenessThreshold) public assetStalenessThreshold;

function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price, , uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    require(price > 0, "ChainlinkOracle: non-positive price");
    require(answeredInRound >= roundId, "ChainlinkOracle: stale round");
    uint256 threshold = assetStalenessThreshold[asset];
    require(threshold == 0 || block.timestamp - updatedAt <= threshold, "ChainlinkOracle: stale price");

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Per-feed staleness thresholds should be stored in a mapping and set by the admin, since different Chainlink feeds have different heartbeat intervals.

## Proof of Concept

1. stETH/ETH Chainlink feed heartbeat = 24 h; last update was 20 h ago at `1.00e18`.
2. stETH depegs to `0.95e18` on secondary markets; the feed has not yet updated.
3. Attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, 0, "")`.
4. `ChainlinkPriceOracle.getAssetPrice(stETH)` returns `1.00e18` (stale). Suppose `rsETHPrice = 1.02e18`.
5. `rsethAmountToMint = (1000e18 * 1.00e18) / 1.02e18 ≈ 980.39 rsETH` is minted.
6. Correct mint at `0.95e18` would be `(1000e18 * 0.95e18) / 1.02e18 ≈ 931.37 rsETH`.
7. Attacker receives `≈ 49 rsETH` excess — approximately `49 * 1.02 ≈ 50 ETH-equivalent` per 1000 stETH deposited, extracted from existing holders.
8. When the feed corrects and `updateRSETHPrice()` is called, `_getTotalEthInProtocol()` computes a lower TVL, `rsETHPrice` drops, and all pre-existing holders are diluted. The attacker retains the excess rsETH.

**Foundry fork test plan:**
- Fork mainnet at a block where the stETH/ETH Chainlink feed is within its heartbeat but the spot price has diverged.
- Deploy or point to the existing `ChainlinkPriceOracle` with the stETH feed.
- Call `depositAsset(stETH, 1000e18, 0, "")` as an unprivileged address.
- Assert that `rsethAmountToMint` exceeds the fair-value amount computed using the spot price.
- Call `updateRSETHPrice()` after warping time past the feed update; assert `rsETHPrice` has decreased, confirming dilution of pre-existing holders.

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

**File:** contracts/LRTOracle.sol (L270-282)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }
```

**File:** contracts/LRTOracle.sol (L339-343)
```text
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```
