Audit Report

## Title
rsETH Transfers Unrestricted While Withdrawal Entry Points Are Paused, Enabling Sale of Non-Redeemable Tokens - (File: contracts/RSETH.sol)

## Summary
The `RSETH._transfer` override enforces only the per-address block list and contains no `whenNotPaused` guard, so rsETH tokens remain freely transferable even when the RSETH contract itself is paused. Independently, the `LRTOracle` price-drop circuit breaker pauses `LRTDepositPool` and `LRTWithdrawalManager` without ever pausing the RSETH token, leaving a window where rsETH can be sold on secondary markets while all withdrawal entry points revert with `Paused`. Buyers who acquire rsETH during this window have their funds temporarily frozen in a non-redeemable state.

## Finding Description
**Root cause 1 — `_transfer` ignores the RSETH pause state.**
`RSETH._transfer` (L287–291) overrides the ERC-20 internal transfer hook but only calls `_enforceNotBlocked`; it never checks `paused()`. Even when `pauseAll()` in `LRTConfig` (L271) explicitly pauses the RSETH contract, `transfer()` and `transferFrom()` continue to succeed because the pause flag is never consulted in the transfer path.

**Root cause 2 — Oracle circuit breaker creates an asymmetric pause.**
`LRTOracle._updateRsETHPrice()` (L278–281) pauses `LRTDepositPool` and `LRTWithdrawalManager` and calls `_pause()` on the oracle itself when the price drop exceeds `pricePercentageLimit`, but it never touches the RSETH token contract. This is structurally different from `LRTConfig.pauseAll()` (L268–271), which does pause RSETH. The result is that the oracle's automated circuit breaker leaves rsETH fully transferable while both `initiateWithdrawal` (L158 `whenNotPaused`) and `instantWithdrawal` (L219 `whenNotPaused`) revert.

**Exploit flow:**
1. A large market move causes `updateRSETHPrice()` (public, callable by anyone) to fire the circuit breaker, pausing `LRTDepositPool` and `LRTWithdrawalManager`.
2. A malicious rsETH holder calls `rsETH.transfer(dex, amount)` or routes a sell through a DEX router — succeeds because `_transfer` has no pause check.
3. A victim buys rsETH on the DEX.
4. Victim calls `LRTWithdrawalManager.initiateWithdrawal(...)` — reverts (`Paused`).
5. Victim calls `LRTWithdrawalManager.instantWithdrawal(...)` — reverts (`Paused`).
6. Victim's rsETH is locked until an admin manually unpauses `LRTWithdrawalManager`; no on-chain indication of when or whether that will occur.

**Existing checks are insufficient:**
- The `isPermanentlyExempt` / `transfersBlockedUntil` mechanism in `_enforceNotBlocked` addresses per-address compliance blocks, not protocol-wide emergency pauses.
- `pauseAll()` does pause RSETH, but (a) it is a manual admin action, not the automated oracle path, and (b) even if RSETH is paused via `pauseAll()`, `_transfer` still has no `whenNotPaused` check, so transfers succeed regardless.

## Impact Explanation
Buyers who acquire rsETH during a pause period cannot convert it back to ETH or LSTs via either `initiateWithdrawal` or `instantWithdrawal` until an admin unpauses `LRTWithdrawalManager`. The duration of the freeze is indeterminate and entirely at admin discretion. This constitutes **Medium — Temporary freezing of funds** within the allowed impact scope.

## Likelihood Explanation
The oracle circuit breaker is triggered by a public, permissionless call to `updateRSETHPrice()` on any sufficiently large price decline. No admin compromise or privileged access is required. Any rsETH holder who monitors oracle state can sell into secondary markets immediately after the circuit breaker fires, while buyers remain unaware that the withdrawal path is closed. The trigger is a routine market event, making this repeatable under normal operating conditions.

## Recommendation
1. Add a `whenNotPaused` check inside `RSETH._transfer` (or override `_beforeTokenTransfer`) so that transfers are blocked when the RSETH contract is paused. Ensure `LRTWithdrawalManager` is added to `isPermanentlyExempt` so that `initiateWithdrawal`'s `safeTransferFrom` call can still receive rsETH even during a pause, or handle this via a dedicated exemption path.
2. Update `LRTOracle._updateRsETHPrice()` to also pause the RSETH token contract when the price-drop circuit breaker fires, making the oracle's automatic pause symmetric with `LRTConfig.pauseAll()`.

## Proof of Concept
```
// Foundry fork test outline
function test_sellRsETHWhileWithdrawalsPaused() public {
    // 1. Simulate price drop beyond pricePercentageLimit
    //    by manipulating oracle inputs so newRsETHPrice < highestRsethPrice * (1 - limit)
    vm.prank(anyone);
    lrtOracle.updateRSETHPrice(); // fires circuit breaker

    // 2. Assert withdrawalManager is paused, RSETH is NOT paused
    assertTrue(lrtWithdrawalManager.paused());
    assertFalse(rsETH.paused());

    // 3. Malicious holder transfers rsETH to victim — succeeds
    vm.prank(maliciousHolder);
    rsETH.transfer(victim, amount); // no revert

    // 4. Victim attempts withdrawal — reverts
    vm.prank(victim);
    vm.expectRevert("Pausable: paused");
    lrtWithdrawalManager.initiateWithdrawal(asset, amount, "");

    vm.prank(victim);
    vm.expectRevert("Pausable: paused");
    lrtWithdrawalManager.instantWithdrawal(asset, amount, "");
    // Victim's rsETH is frozen until admin unpauses
}
```