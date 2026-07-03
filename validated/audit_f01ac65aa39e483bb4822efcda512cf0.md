Audit Report

## Title
Unrestricted `updateRSETHPrice()` Allows Any Caller to Trigger Protocol-Wide Pause on Price Downtick, Causing Temporary Fund Freeze — (File: contracts/LRTOracle.sol)

## Summary
`LRTOracle.updateRSETHPrice()` is a public function with no access control beyond `whenNotPaused`. When the computed rsETH price falls below `highestRsethPrice × (1 − pricePercentageLimit)`, the function atomically pauses `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle` itself, and returns without updating `rsETHPrice`. Any unprivileged EOA can call this function immediately after any on-chain price-decreasing event to freeze all deposits and withdrawals until an admin manually unpauses each contract.

## Finding Description
`updateRSETHPrice()` carries no role restriction:

```solidity
// contracts/LRTOracle.sol L87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

Inside `_updateRsETHPrice()`, the downside-protection branch at lines 270–282 fires when `newRsETHPrice < highestRsethPrice` and the absolute difference exceeds `pricePercentageLimit.mulWad(highestRsethPrice)`:

```solidity
if (isPriceDecreaseOffLimit) {
    if (!lrtDepositPool.paused()) lrtDepositPool.pause();
    if (!withdrawalManager.paused()) withdrawalManager.pause();
    _pause();
    return;   // rsETHPrice is NOT updated here
}
```

Three consequences fire atomically:
1. `LRTDepositPool` is paused — `depositETH`/`depositAsset` revert.
2. `LRTWithdrawalManager` is paused — `initiateWithdrawal`, `completeWithdrawal`, `instantWithdrawal`, and `unlockQueue` all revert.
3. `LRTOracle` itself is paused — `updateRSETHPrice()` can no longer be called.

Because the function returns early at line 281, `rsETHPrice` (line 313) is never updated. The stale pre-drop price persists in storage and is read directly by `getExpectedAssetAmount`:

```solidity
// contracts/LRTWithdrawalManager.sol L593
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

Recovery requires `onlyLRTAdmin` to call `unpause()` on each of the three contracts individually (LRTOracle L143, LRTWithdrawalManager L352). There is no automatic recovery or time-bounded grace period. The privileged escape hatch `updateRSETHPriceAsManager()` (L94) exists but is not called automatically on unpause, so the stale price persists unless the admin explicitly invokes it.

The upside-price path (lines 260–266) correctly restricts non-manager callers by reverting with `PriceAboveDailyThreshold`, but the downside path has no equivalent restriction — it silently pauses instead of reverting for non-managers.

## Impact Explanation
**Medium — Temporary freezing of funds.** All user deposits and all pending withdrawals (initiate, complete, instant, and queue unlock) are inaccessible for the duration of the pause. The freeze is not permanent because an admin can unpause, but it requires privileged manual intervention with no time bound. The secondary stale-price effect (rsETHPrice not updated on the pause path) means that if the admin unpauses without first calling `updateRSETHPriceAsManager()`, withdrawal accounting over-values rsETH at the pre-drop price, paying out more underlying than the post-slashing rate warrants.

## Likelihood Explanation
The attacker requires zero capital and only pays gas for a single public call. The only preconditions are: (1) `pricePercentageLimit > 0` (a non-zero value is the operationally meaningful configuration), and (2) a qualifying price-decreasing event has occurred on-chain (EigenLayer slashing, LST oracle correction). Both conditions are realistic and recurring. The attack is repeatable: after each admin unpause, the same call can be made again if the price remains below the threshold. No front-running of a specific transaction is required — the attacker simply observes the on-chain state and calls the function.

## Recommendation
1. **Restrict `updateRSETHPrice()` to authorised callers** (e.g., `onlyLRTManager` or a dedicated keeper role), mirroring the access control already present on `updateRSETHPriceAsManager()`. The public variant provides no additional user benefit that justifies the open pause surface.
2. **Update `rsETHPrice` before returning** on the pause path (move `rsETHPrice = newRsETHPrice` before the `return` at line 281), so the stored price reflects reality when the protocol is unpaused and prevents stale-price accounting errors in `getExpectedAssetAmount`.
3. **Introduce a grace period or confirmation window** before the pause fires, to distinguish transient oracle noise from genuine slashing events and reduce the attack surface for timing-based triggering.

## Proof of Concept
```
Precondition:
  pricePercentageLimit = 5e16 (5%)
  highestRsethPrice    = 1.10e18 (set during a prior price peak)

Step 1. An EigenLayer slashing event reduces totalETHInProtocol.
        LST oracle reflects the loss; newRsETHPrice computes to ~1.03e18.
        diff = 1.10e18 - 1.03e18 = 0.07e18
        threshold = 0.05 × 1.10e18 = 0.055e18
        0.07e18 > 0.055e18 → isPriceDecreaseOffLimit = true

Step 2. Attacker (any EOA) calls:
            LRTOracle.updateRSETHPrice()

Step 3. _updateRsETHPrice() executes lines 277-281:
            lrtDepositPool.pause()        // deposits frozen
            withdrawalManager.pause()     // withdrawals frozen
            _pause()                      // oracle frozen
            return                        // rsETHPrice NOT updated (stays at 1.10e18)

Step 4. All calls to depositETH, depositAsset, initiateWithdrawal,
        completeWithdrawal, instantWithdrawal, and unlockQueue revert
        with "Pausable: paused" until admin calls unpause() on each contract.

Step 5. rsETHPrice remains at 1.10e18 (stale). If admin unpauses without
        calling updateRSETHPriceAsManager(), getExpectedAssetAmount()
        returns inflated values, over-paying withdrawers relative to the
        true post-slash rate.

Foundry test sketch:
  1. Deploy LRTOracle, LRTDepositPool, LRTWithdrawalManager with mocks.
  2. Set pricePercentageLimit = 5e16, highestRsethPrice = 1.10e18.
  3. Mock _getTotalEthInProtocol() to return a value yielding newRsETHPrice = 1.03e18.
  4. Call lrtOracle.updateRSETHPrice() from address(0xdead) (unprivileged).
  5. Assert lrtDepositPool.paused() == true.
  6. Assert lrtWithdrawalManager.paused() == true.
  7. Assert lrtOracle.paused() == true.
  8. Assert lrtOracle.rsETHPrice() == 1.10e18 (stale, not updated).
```