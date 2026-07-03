Audit Report

## Title
`whenNotPaused` on `unlockQueue` and `completeWithdrawal` Temporarily Freezes In-Flight Withdrawal rsETH - (File: contracts/LRTWithdrawalManager.sol)

## Summary
Users who call `initiateWithdrawal` transfer their rsETH into `LRTWithdrawalManager` before any queue processing occurs. Both `unlockQueue` and `completeWithdrawal` carry `whenNotPaused` modifiers, and no cancellation path exists. When the contract is legitimately paused in response to an emergency, all rsETH already committed to pending withdrawal requests is frozen in the contract with no user-accessible recovery mechanism until the contract is unpaused.

## Finding Description
The withdrawal lifecycle is a two-step commit-then-claim design:

**Step 1 — `initiateWithdrawal`** (L150–178): The user's rsETH is pulled into the contract via `safeTransferFrom` at L166. A `WithdrawalRequest` is recorded and the nonce is advanced. The rsETH is now held by `LRTWithdrawalManager`.

**Step 2a — `unlockQueue`** (L268–320): The operator calls this to burn the queued rsETH and redeem assets from the unstaking vault. It carries `whenNotPaused` at L279. While paused, this call reverts.

**Step 2b — `completeWithdrawal`** (L183–185): The user calls this to receive their asset. It carries `whenNotPaused` at L183. While paused, this call also reverts.

There is no `cancelWithdrawal` or equivalent function anywhere in the contract that would allow a user to reclaim their rsETH from a pending (not yet unlocked) request. A full read of the contract confirms no such path exists.

The result: any rsETH transferred at `initiateWithdrawal` before a pause is stranded in the contract for the entire pause duration. The user cannot unlock, complete, or cancel their request.

## Impact Explanation
**Medium — Temporary freezing of funds.**

Every user with a pending withdrawal request at the moment of a pause has their rsETH locked in `LRTWithdrawalManager` with no self-help path. The freeze duration is bounded only by how long the pause lasts. The rsETH is not lost permanently (the pause can be lifted by `onlyLRTAdmin` via `unpause`), but it is inaccessible for an unbounded period, matching the "Temporary freezing of funds" impact class.

## Likelihood Explanation
The `PAUSER_ROLE` is explicitly provisioned for emergency response (L347–349). Any security incident — oracle manipulation, exploit attempt, bridge anomaly — that triggers a legitimate pause while withdrawal requests are in-flight activates the freeze. No attacker action is required; the pauser acts in good faith. The combination of pending withdrawals and a pause is a realistic, foreseeable operational state. The freeze affects all users with in-flight requests simultaneously.

## Recommendation
Remove `whenNotPaused` from `unlockQueue` so the queue can be drained during a pause, allowing users to call `completeWithdrawal` once unpaused (or remove `whenNotPaused` from `completeWithdrawal` as well for already-unlocked requests). Alternatively, add a `cancelWithdrawal` function that returns the user's rsETH when their request has not yet been unlocked (`userNonce >= nextLockedNonce[asset]`), callable regardless of pause state.

## Proof of Concept
1. Alice calls `initiateWithdrawal(stETH, 1e18, "ref")` while the contract is unpaused. `safeTransferFrom` pulls `1e18` rsETH from Alice into `LRTWithdrawalManager` at L166. A `WithdrawalRequest` is stored; `nextUnusedNonce[stETH]` advances.
2. A security incident occurs; `PAUSER_ROLE` calls `pause()` (L347).
3. Operator calls `unlockQueue(stETH, ...)` → reverts with `Pausable: paused` at the `whenNotPaused` check (L279).
4. Alice calls `completeWithdrawal(stETH, "ref")` → reverts with `Pausable: paused` at L183.
5. No `cancelWithdrawal` function exists. Alice's `1e18` rsETH remains locked in `LRTWithdrawalManager` until `unpause()` is called by `onlyLRTAdmin`.

**Foundry test sketch:**
```solidity
function test_pauseFreezesInFlightWithdrawal() public {
    // Alice initiates withdrawal (contract not paused)
    vm.prank(alice);
    withdrawalManager.initiateWithdrawal(stETH, 1e18, "ref");

    // Emergency pause
    vm.prank(pauser);
    withdrawalManager.pause();

    // unlockQueue reverts
    vm.prank(operator);
    vm.expectRevert("Pausable: paused");
    withdrawalManager.unlockQueue(stETH, 1, minAssetPrice, minRsEthPrice, maxAssetPrice, maxRsEthPrice);

    // completeWithdrawal reverts
    vm.prank(alice);
    vm.expectRevert("Pausable: paused");
    withdrawalManager.completeWithdrawal(stETH, "ref");

    // Alice's rsETH is confirmed held by the contract
    assertEq(rsETH.balanceOf(address(withdrawalManager)), 1e18);
}
```