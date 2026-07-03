Audit Report

## Title
Temporary Freezing of Remaining Staked KERNEL Balance When All Withdrawal Slots Are Filled — (`contracts/KERNEL/KernelDepositPool.sol`)

## Summary
`initiateWithdrawal` enforces a hard cap on open withdrawal slots per user and immediately deducts the requested amount from `balanceOf`. Once all slots are filled, any remaining `balanceOf[user]` becomes inaccessible until the earliest `unlockTime` passes. The contract provides no `cancelWithdrawal` escape hatch, leaving the remaining staked balance frozen for up to `MAX_WITHDRAWAL_DELAY = 30 days`.

## Finding Description
`initiateWithdrawal` checks `userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser` and reverts with `WithdrawalLimitReached` once the cap is reached (L323). The requested amount is subtracted from `balanceOf[msg.sender]` and `totalKernelStaked` before the slot check can be bypassed (L325–326). `claimWithdrawal` enforces `block.timestamp < withdrawal.unlockTime → revert WithdrawalNotReady` (L355–357). There is no function in the contract that cancels a pending withdrawal or returns its amount to `balanceOf`. The only functions that increase `balanceOf` are `stake` and `stakeFor`; neither helps a user who has already moved funds into pending withdrawals. Once all N slots are filled with `unlockTime = block.timestamp + withdrawalDelay`, the remaining `balanceOf[user]` cannot be accessed via `initiateWithdrawal` (slot cap), `claimWithdrawal` (time lock), or any other path.

## Impact Explanation
**Medium — Temporary freezing of funds.** A user's remaining staked KERNEL (`balanceOf[user] > 0`) is completely inaccessible for up to `MAX_WITHDRAWAL_DELAY = 30 days`. The funds are not permanently lost but are frozen for the full delay window, matching the allowed impact class "Medium. Temporary freezing of funds."

## Likelihood Explanation
**Low-Medium.** Requires: (1) admin has set `withdrawalDelay` to a non-trivial value (up to 30 days, enforced by `setWithdrawalDelay`); (2) a user fills all `maxNumberOfWithdrawalsPerUser` slots (configurable up to `MAX_WITHDRAWALS_PER_USER = 100`) with partial withdrawals leaving a non-zero `balanceOf`. No external attacker is required; the user's own routine withdrawal behavior is sufficient. The scenario is plausible for users who habitually initiate many small withdrawals without awareness of the slot cap.

## Recommendation
Add a `cancelWithdrawal(uint256 _withdrawalId)` function that: (1) verifies `withdrawals[_withdrawalId].user == msg.sender` and `!withdrawal.claimed`; (2) removes the ID from `userWithdrawalIds[msg.sender]`; (3) credits `withdrawal.amount` back to `balanceOf[msg.sender]` and `totalKernelStaked`. This provides an escape hatch to free slots and recover staked balance without waiting for the delay to expire.

## Proof of Concept
```solidity
// Preconditions:
// - withdrawalDelay = 30 days
// - maxNumberOfWithdrawalsPerUser = N (e.g., 10)
// - user has staked 1000e18 KERNEL

// Step 1: Fill all N slots
for (uint i = 0; i < N; i++) {
    pool.initiateWithdrawal(1e18);
}
// balanceOf[user] = 1000e18 - N*1e18 > 0
// userWithdrawalIds[user].length == N == maxNumberOfWithdrawalsPerUser
// All unlockTimes = block.timestamp + 30 days

// Step 2: initiateWithdrawal reverts — slot cap hit
vm.expectRevert(KernelDepositPool.WithdrawalLimitReached.selector);
pool.initiateWithdrawal(1e18);

// Step 3: claimWithdrawal reverts for all slots — time lock active
uint256[] memory ids = pool.getUserWithdrawalIds(user);
for (uint i = 0; i < ids.length; i++) {
    vm.expectRevert(KernelDepositPool.WithdrawalNotReady.selector);
    pool.claimWithdrawal(ids[i]);
}
// Remaining balanceOf[user] > 0 is inaccessible for up to 30 days.
```