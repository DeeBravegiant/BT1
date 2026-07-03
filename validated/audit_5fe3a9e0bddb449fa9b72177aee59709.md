Audit Report

## Title
Stale `rsETHPrice` During Price-Threshold Revert Enables Excess wrsETH Minting on L2, Diluting Existing Holders' Yield — (File: `contracts/LRTOracle.sol`)

## Summary

When the computed rsETH price rises above `highestRsethPrice` by more than `pricePercentageLimit`, `LRTOracle._updateRsETHPrice()` reverts with `PriceAboveDailyThreshold` for any non-manager caller, leaving `rsETHPrice` at its previous (lower) value. Both `RSETHRateProvider` and `RSETHMultiChainRateProvider` read this stale value directly and broadcast it to L2. `RSETHPoolV3` on L2 then uses the stale (lower) rate to compute wrsETH mint amounts, causing new depositors to receive excess wrsETH at the permanent expense of existing holders' accrued yield.

## Finding Description

In `LRTOracle._updateRsETHPrice()`, the price update guard at lines 252–266 reverts for non-manager callers when the price increase exceeds the configured limit:

```solidity
// contracts/LRTOracle.sol lines 252-266
if (newRsETHPrice > highestRsethPrice) {
    uint256 priceDifference = newRsETHPrice - highestRsethPrice;
    bool isPriceIncreaseOffLimit =
        pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
    if (isPriceIncreaseOffLimit) {
        if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
            revert PriceAboveDailyThreshold();
        }
    }
}
```

The assignment `rsETHPrice = newRsETHPrice` at line 313 is never reached when this revert fires. The public entry point `updateRSETHPrice()` (lines 87–89) is gated only by `whenNotPaused`, so any keeper or EOA can call it but will receive a revert for the entire staleness window. The only escape hatch is `updateRSETHPriceAsManager()` (lines 94–96), restricted to `MANAGER`.

The stale `rsETHPrice` propagates cross-chain via:
- `RSETHRateProvider.getLatestRate()` → `ILRTOracle(rsETHPriceOracle).rsETHPrice()` (line 28)
- `RSETHMultiChainRateProvider.getLatestRate()` → same (line 27)

On L2, `RSETHPoolV3.viewSwapRsETHAmountAndFee()` (lines 299–308) computes:
```solidity
uint256 rsETHToETHrate = getRate();          // stale, lower than actual
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

A stale (lower) denominator inflates `rsETHAmount`. The excess wrsETH minted to new depositors permanently dilutes the share of accrued yield belonging to existing holders. No attacker action is required; any depositor transacting during the staleness window captures yield that belongs to prior holders.

## Impact Explanation

**High — Theft of unclaimed yield.**

Existing rsETH/wrsETH holders have accrued yield reflected in the rising `rsETHPrice`. When new depositors receive excess wrsETH at the stale (lower) rate, the total wrsETH supply is inflated beyond what the underlying ETH supports. After the manager corrects the oracle, each wrsETH unit is worth less than it would have been — the difference is a permanent, irreversible transfer of accrued yield from existing holders to the new depositors who minted during the stale window. The excess tokens cannot be clawed back.

## Likelihood Explanation

**Low-Medium.**

- No attacker is required; the condition is triggered by normal EigenLayer reward distributions or any event that causes a single-cycle price jump exceeding `pricePercentageLimit`.
- `pricePercentageLimit` is admin-configurable; a conservative setting (e.g., 0.5%–1%) makes the threshold reachable during routine reward accrual.
- The staleness window persists until the manager manually calls `updateRSETHPriceAsManager()`. Any L2 deposits processed during this window are permanently affected.
- The condition is repeatable every reward cycle if the limit is set tightly.

## Recommendation

1. **Do not revert for non-manager callers on upside threshold breach.** Instead, cap the accepted price at `highestRsethPrice + pricePercentageLimit.mulWad(highestRsethPrice)` and update `rsETHPrice` to the capped value, emitting an event. Reserve the full uncapped update for the manager path only.
2. **Add a staleness heartbeat check** in `RSETHRateProvider.getLatestRate()` and `RSETHMultiChainRateProvider.getLatestRate()`: revert or return a sentinel value if `rsETHPrice` has not been updated within a configurable window, preventing L2 pools from consuming a stale rate.
3. **Alternatively**, pause L2 deposits (via the `RSETHPoolV3` pauser role) atomically whenever the threshold revert condition is detected, preventing any minting against the stale rate until the manager resolves it.

## Proof of Concept

1. Deploy with `highestRsethPrice = 1.05e18`, `pricePercentageLimit = 1e16` (1%).
2. EigenLayer distributes rewards; `_getTotalEthInProtocol()` yields `newRsETHPrice = 1.062e18` (1.14% increase, above the 1% limit).
3. Keeper calls `updateRSETHPrice()`. Guard at line 257 sets `isPriceIncreaseOffLimit = true`. Keeper is not manager → revert `PriceAboveDailyThreshold`. `rsETHPrice` remains `1.05e18`.
4. `RSETHRateProvider` broadcasts stale `1.05e18` to L2 via LayerZero.
5. Depositor on L2 sends 1 ETH. `viewSwapRsETHAmountAndFee(1e18)` computes `rsETHAmount = 1e18 * 1e18 / 1.05e18 ≈ 0.9524 wrsETH` instead of the correct `1e18 / 1.062e18 ≈ 0.9416 wrsETH`. Excess: ~0.0108 wrsETH per ETH.
6. Manager eventually calls `updateRSETHPriceAsManager()`; oracle corrects to `1.062e18`. The excess wrsETH already minted is permanent — existing holders' yield is irreversibly diluted.

**Foundry fork test outline:**
```solidity
function testStaleRateMintExcess() public {
    // Fork mainnet, set pricePercentageLimit = 1e16
    // Simulate reward accrual pushing newRsETHPrice above threshold
    vm.expectRevert(LRTOracle.PriceAboveDailyThreshold.selector);
    lrtOracle.updateRSETHPrice(); // non-manager call reverts
    
    uint256 staleRate = rateProvider.getLatestRate();
    assertEq(staleRate, 1.05e18); // still stale
    
    uint256 rsETHMinted = pool.viewSwapRsETHAmountAndFee(1e18);
    uint256 correctRate = 1.062e18;
    uint256 correctMint = 1e18 * 1e18 / correctRate;
    assertGt(rsETHMinted, correctMint); // excess minted
}
```