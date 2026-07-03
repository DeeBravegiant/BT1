Audit Report

## Title
Missing Staleness Validation in `ChainlinkPriceOracle.getAssetPrice()` Allows Stale Price to Drive rsETH Minting - (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all return values except `answer`, performing no staleness or round-completeness validation. This stale price is consumed live during every deposit via `LRTDepositPool.getRsETHAmountToMint()`, which divides the stale asset price by the stored `rsETHPrice` to determine how many rsETH tokens to mint. An attacker who monitors oracle staleness on-chain can time a deposit during a staleness window to receive more rsETH than deserved, diluting existing holders.

## Finding Description

In `contracts/oracles/ChainlinkPriceOracle.sol` at line 52, `getAssetPrice()` destructures only `answer` from `latestRoundData()`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

`roundId`, `startedAt`, `updatedAt`, and `answeredInRound` are all silently discarded. There is no check for `updatedAt != 0`, `block.timestamp - updatedAt <= maxStaleness`, or `answeredInRound >= roundId`.

This contrasts directly with `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol`, which validates all three conditions:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
``` [2](#0-1) 

The exploit path is fully reachable by any unprivileged depositor:

1. `LRTDepositPool.depositAsset()` calls `_beforeDeposit()` → `getRsETHAmountToMint()`.
2. `getRsETHAmountToMint()` calls `lrtOracle.getAssetPrice(asset)` **live** (not from a cached value) and divides by the stored `rsETHPrice`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

3. `LRTOracle.getAssetPrice()` delegates directly to `ChainlinkPriceOracle.getAssetPrice()` with no additional staleness guard: [4](#0-3) 

The `minRSETHAmountExpected` slippage parameter in `depositAsset()` protects only the depositor from receiving *fewer* rsETH than expected — it does not protect existing rsETH holders from dilution when the attacker intentionally deposits at an inflated stale price. [5](#0-4) 

The `pricePercentageLimit` guard in `_updateRsETHPrice()` only fires when `updateRSETHPrice()` is called separately; it does not gate individual deposits. [6](#0-5) 

## Impact Explanation

If a Chainlink LST/ETH feed goes stale with a price higher than the true current value, an attacker deposits LST and receives more rsETH than the deposited collateral warrants. This over-minting dilutes the pro-rata ETH backing of every existing rsETH holder — a concrete, quantifiable loss of value to current holders. This maps to **Low: Contract fails to deliver promised returns** at minimum, with escalation toward **High: Theft of unclaimed yield** in a prolonged staleness scenario (e.g., L2 sequencer downtime) where the price divergence is material and the attacker can deposit up to the per-asset deposit limit.

## Likelihood Explanation

Chainlink feeds on L2 networks (Arbitrum, Optimism, Base) can go stale during sequencer downtime. The `updatedAt` timestamp is publicly readable on-chain, so any attacker can monitor staleness without any privileged access. No special role is required — `depositAsset()` is a public, permissionless function. The attack is repeatable for as long as the feed remains stale and deposit capacity remains.

## Recommendation

Mirror the validation already present in `ChainlinkOracleForRSETHPoolCollateral` by adding staleness and round-completeness checks to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (block.timestamp - updatedAt > MAX_STALENESS) revert StalePrice();
    if (price <= 0) revert InvalidPrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`MAX_STALENESS` should be configurable per feed (or set to the Chainlink heartbeat + buffer, e.g., 3600s for 1-hour heartbeat feeds).

## Proof of Concept

**Foundry fork test outline:**

```solidity
// Fork mainnet/Arbitrum at a block where a supported LST/ETH feed is stale
// (or mock the Chainlink aggregator to return a stale updatedAt)

function testStaleOracleMinting() public {
    // 1. Deploy/fork with ChainlinkPriceOracle registered for wstETH
    // 2. Mock aggregator: set updatedAt = block.timestamp - 4 hours,
    //    price = 1.10e18 (true price = 1.05e18, 5% inflated)
    // 3. Record rsETHPrice and totalSupply before deposit
    uint256 priceBefore = lrtOracle.rsETHPrice();
    uint256 supplyBefore = rsETH.totalSupply();

    // 4. Attacker deposits 1e18 wstETH
    vm.prank(attacker);
    lrtDepositPool.depositAsset(wstETH, 1e18, 0, "");

    // 5. Assert attacker received more rsETH than fair share
    uint256 fairRsETH = (1e18 * 1.05e18) / priceBefore;
    uint256 actualRsETH = rsETH.balanceOf(attacker);
    assertGt(actualRsETH, fairRsETH); // attacker over-minted

    // 6. Assert existing holders are diluted: rsETHPrice after updateRSETHPrice
    //    is lower than priceBefore (collateral backing per rsETH decreased)
    lrtOracle.updateRSETHPrice();
    assertLt(lrtOracle.rsETHPrice(), priceBefore);
}
```

The test requires no privileged access and is triggerable on any fork where a registered Chainlink feed's `updatedAt` is beyond the heartbeat threshold.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L30-32)
```text
        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();
```

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTDepositPool.sol (L667-669)
```text
        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L252-266)
```text
        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
```
