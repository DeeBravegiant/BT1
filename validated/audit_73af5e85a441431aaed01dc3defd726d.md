Audit Report

## Title
`completeWithdrawal` and `completeWithdrawalForUser` blocked by `whenNotPaused` after rsETH already transferred to contract - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`LRTWithdrawalManager.initiateWithdrawal` transfers the user's rsETH into the contract before the withdrawal is finalised. Both `completeWithdrawal` and `completeWithdrawalForUser` carry the `whenNotPaused` modifier, so any pause issued after initiation leaves the already-transferred rsETH inaccessible for the entire duration of the pause. No cancel, refund, or pause-exempt completion path exists.

## Finding Description
The two-step withdrawal flow is:

1. `initiateWithdrawal` (line 158, `whenNotPaused`) — pulls rsETH from the user via `safeTransferFrom` at line 166, records a `WithdrawalRequest`, and increments `assetsCommitted`.
2. `completeWithdrawal` (line 183, `whenNotPaused`) / `completeWithdrawalForUser` (line 199, `whenNotPaused`) — transfers the committed LST/ETH to the user.

`pause()` (line 347) is callable by any address holding `PAUSER_ROLE`, a role that is explicitly separate from and lower-privilege than `LRT_ADMIN_ROLE`. `unpause()` (line 352) requires `onlyLRTAdmin`. Once paused, every path that could return funds to the user — `completeWithdrawal`, `completeWithdrawalForUser`, and `unlockQueue` (line 279, also `whenNotPaused`) — is blocked. There is no `cancelWithdrawal`, no emergency-refund function, and no pause-exempt variant of the completion functions. The rsETH held by the contract and the `assetsCommitted` accounting entry are both frozen until an admin calls `unpause`.

## Impact Explanation
Any user who has called `initiateWithdrawal` and whose request has not yet been completed is unable to recover their rsETH or receive the corresponding LST/ETH for the full duration of the pause. This is a concrete **temporary freezing of funds** matching the allowed Medium impact class. The freeze is not hypothetical: the rsETH is held by the contract (line 166), the corresponding asset is reserved in `assetsCommitted` (line 173), and every exit path is gated by `whenNotPaused`.

## Likelihood Explanation
`PAUSER_ROLE` is a routine operational role used during security incidents or upgrades. The exposure window between `initiateWithdrawal` and `completeWithdrawal` is at least `withdrawalDelayBlocks` (initialised to `8 days / 12 seconds` ≈ 57,600 blocks, line 94), giving a wide window during which a pause can trap funds. No attacker capability is required; the condition arises from normal protocol operations.

## Recommendation
Remove `whenNotPaused` from `completeWithdrawal` and `completeWithdrawalForUser`. Pausing should prevent new rsETH from entering the contract (i.e., block `initiateWithdrawal` and `instantWithdrawal`) but must never block the return of funds already surrendered. Optionally, add a `cancelWithdrawal` function that refunds rsETH to the user even while paused, as an additional safety valve.

## Proof of Concept
1. Alice calls `initiateWithdrawal(stETH, 1e18, "")`. The contract executes `safeTransferFrom(Alice, address(this), 1e18)` at line 166 and records her request.
2. An address holding `PAUSER_ROLE` calls `pause()` (line 347).
3. After `withdrawalDelayBlocks` pass and an operator calls `unlockQueue` (which is also blocked by `whenNotPaused` at line 279 — so the queue cannot even be unlocked while paused), Alice calls `completeWithdrawal(stETH, "")`. The call reverts at the `whenNotPaused` modifier on line 183.
4. `completeWithdrawalForUser` is equally blocked by `whenNotPaused` at line 199.
5. Alice's `1e18` rsETH remains locked in the contract for the entire duration of the pause with no recovery path.

Foundry test sketch:
```solidity
function test_pauseTrapsRsETH() public {
    // Alice initiates withdrawal, rsETH transferred to contract
    vm.prank(alice);
    withdrawalManager.initiateWithdrawal(stETH, 1e18, "");
    assertEq(rsETH.balanceOf(address(withdrawalManager)), 1e18);

    // Pauser pauses
    vm.prank(pauser);
    withdrawalManager.pause();

    // After delay, Alice tries to complete — must revert
    vm.roll(block.number + withdrawalManager.withdrawalDelayBlocks() + 1);
    vm.prank(alice);
    vm.expectRevert("Pausable: paused");
    withdrawalManager.completeWithdrawal(stETH, "");

    // rsETH still locked
    assertEq(rsETH.balanceOf(address(withdrawalManager)), 1e18);
}
```