Audit Report

## Title
Unvalidated Raw Chainlink Response Passthrough Enables Stale-Price-Triggered Protocol Freeze - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` and discards every return field except `price`, performing no staleness, round-completeness, or non-negative price check. Because `LRTOracle.updateRSETHPrice()` is a public, permissionless function, any external caller can trigger a price update during a stale Chainlink feed window, causing the protocol to compute a depressed `rsETHPrice` and automatically pause `LRTDepositPool` and `LRTWithdrawalManager`, freezing all user deposits and withdrawals until an admin manually intervenes.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` silently drops all fields except `price`:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L52-54
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

No check is made on `updatedAt` (staleness), `answeredInRound >= roundId` (round completeness), or `price > 0` (validity). The same repository already implements all three guards in `ChainlinkOracleForRSETHPoolCollateral.getRate()`:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L30-32
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0)            revert IncompleteRound();
if (ethPrice <= 0)             revert InvalidPrice();
``` [2](#0-1) 

The unvalidated price propagates through the full call chain:

- `LRTOracle.updateRSETHPrice()` (public, no role check) [3](#0-2) 
- → `_updateRsETHPrice()` → `_getTotalEthInProtocol()` calls `getAssetPrice(asset)` [4](#0-3) 
- → `ChainlinkPriceOracle.getAssetPrice()` returns the stale, unvalidated price [5](#0-4) 
- → `newRsETHPrice` is computed from the depressed TVL [6](#0-5) 

The auto-pause fires when `newRsETHPrice` drops below `highestRsethPrice` by more than `pricePercentageLimit`:

```solidity
// contracts/LRTOracle.sol L270-281
if (newRsETHPrice < highestRsethPrice) {
    ...
    if (isPriceDecreaseOffLimit) {
        if (!lrtDepositPool.paused()) lrtDepositPool.pause();
        if (!withdrawalManager.paused()) withdrawalManager.pause();
        _pause();
        return;
    }
}
``` [7](#0-6) 

## Impact Explanation
**Medium — Temporary freezing of funds.** When the auto-pause triggers, `LRTDepositPool` and `LRTWithdrawalManager` are both paused, blocking all user deposits and withdrawals until an admin calls `unpause()`. This matches the allowed impact class "Temporary freezing of funds." The freeze is not permanent because an admin can recover, but it is externally triggerable by any unprivileged caller and can be repeated each time the admin unpauses if the underlying staleness condition persists.

## Likelihood Explanation
**Medium.** Chainlink feeds go stale during L1 congestion, L2 sequencer downtime, or when the price deviation threshold is not crossed for an extended period — all documented, recurring conditions. The entry point `updateRSETHPrice()` requires no role or privilege. An attacker only needs to monitor the `updatedAt` field of any registered Chainlink feed off-chain and call `updateRSETHPrice()` during a stale window where the reported price is depressed by more than `pricePercentageLimit` relative to `highestRsethPrice`. The attack is repeatable and cheap (a single public call).

## Recommendation
Apply the same three guards already present in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound)
    = priceFeed.latestRoundData();

if (answeredInRound < roundId)                  revert StalePrice();
if (updatedAt == 0)                             revert IncompleteRound();
if (price <= 0)                                 revert InvalidPrice();
// optionally: if (block.timestamp - updatedAt > MAX_STALENESS) revert StalePrice();
```

## Proof of Concept
**Foundry fork test outline:**

1. Fork mainnet/Holesky at a block where a registered Chainlink feed (e.g., stETH/ETH) has a recent `updatedAt`.
2. Warp `block.timestamp` forward past the feed's heartbeat interval without advancing the feed (simulating staleness). The feed's `updatedAt` remains old; `answeredInRound` may equal `roundId` but the price is now stale.
3. Alternatively, use a mock `AggregatorV3Interface` that returns `answeredInRound < roundId` or `updatedAt == 0` or a price significantly below the current `highestRsethPrice`.
4. Call `LRTOracle.updateRSETHPrice()` from an unprivileged EOA.
5. Assert that `lrtDepositPool.paused() == true`, `withdrawalManager.paused() == true`, and `lrtOracle.paused == true`.
6. Confirm no privileged role was used at any step.

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

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L270-281)
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
```

**File:** contracts/LRTOracle.sol (L339-339)
```text
            uint256 assetER = getAssetPrice(asset);
```
