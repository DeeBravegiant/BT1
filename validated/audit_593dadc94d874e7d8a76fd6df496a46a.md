Audit Report

## Title
`KernelDepositPool.initialize()` Fails to Set `withdrawalDelay` and `maxNumberOfWithdrawalsPerUser`, Causing Immediate Withdrawal Freeze and Time-Lock Bypass - (File: contracts/KERNEL/KernelDepositPool.sol)

## Summary
`KernelDepositPool.initialize()` never assigns `withdrawalDelay` or `maxNumberOfWithdrawalsPerUser`, leaving both at the Solidity default of `0`. This immediately causes `initiateWithdrawal()` to always revert for every user (temporary fund freeze), and once the admin fixes that, the zero `withdrawalDelay` means every withdrawal is claimable in the same block it is initiated, eliminating the intended time-lock.

## Finding Description
`initialize()` (L259–271) sets only `kernelToken`, `rewardsToken`, and admin roles. It never calls `setWithdrawalDelay()` or `setMaxNumberOfWithdrawalsPerUser()`, so both storage variables remain `0` after deployment.

**Effect 1 – Withdrawal freeze:**
`initiateWithdrawal()` at L323 checks:
```solidity
if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();
```
With `maxNumberOfWithdrawalsPerUser == 0`, the condition `0 >= 0` is always `true`. Every call to `initiateWithdrawal()` reverts unconditionally. All staked KERNEL tokens are frozen until the admin calls `setMaxNumberOfWithdrawalsPerUser()`.

**Effect 2 – Zero-delay withdrawal:**
L330 computes `unlockTime = block.timestamp + withdrawalDelay`. With `withdrawalDelay == 0`, `unlockTime == block.timestamp`. The `claimWithdrawal()` guard at L355:
```solidity
if (block.timestamp < withdrawal.unlockTime) revert WithdrawalNotReady();
```
evaluates as `block.timestamp < block.timestamp` → `false`, so the claim passes immediately in the same block, bypassing the intended time-lock entirely.

The setter functions (`setWithdrawalDelay` L598, `setMaxNumberOfWithdrawalsPerUser` L610) exist but are not invoked during initialization, and there is no `reinitializer` to set them post-deployment.

## Impact Explanation
**Primary (Medium – Temporary freezing of funds):** From the moment of deployment until the admin calls `setMaxNumberOfWithdrawalsPerUser()`, `initiateWithdrawal()` is permanently broken for all users. Any KERNEL tokens staked via `stake()` or `stakeFor()` have no exit path. The freeze is automatic and requires no attacker action.

**Secondary (Low – Contract fails to deliver promised returns):** After the admin fixes Effect 1, if `setWithdrawalDelay()` has not been called, the time-lock provides zero protection. A user can stake, initiate, and claim a withdrawal atomically within one transaction or block, defeating the protocol's stated withdrawal delay guarantee without losing principal.

## Likelihood Explanation
The freeze is automatic upon deployment — no attacker action is required. Any user who stakes KERNEL tokens before the admin executes `setMaxNumberOfWithdrawalsPerUser()` is immediately affected. Since `initialize()` is a single transaction and the setters are separate transactions, there is a realistic deployment window during which users can stake but cannot withdraw. The condition is deterministic and 100% reproducible on any fresh deployment.

## Recommendation
Initialize both variables inside `initialize()` with validated non-zero values, either as parameters or hardcoded safe defaults:

```solidity
function initialize(
    address _admin,
    address _kernelToken,
    address _rewardToken,
    uint256 _withdrawalDelay,
    uint256 _maxNumberOfWithdrawalsPerUser
) external initializer {
    // ... existing checks ...
    require(_withdrawalDelay > 0 && _withdrawalDelay <= MAX_WITHDRAWAL_DELAY, "bad delay");
    require(_maxNumberOfWithdrawalsPerUser > 0 && _maxNumberOfWithdrawalsPerUser <= MAX_WITHDRAWALS_PER_USER, "bad max");
    withdrawalDelay = _withdrawalDelay;
    maxNumberOfWithdrawalsPerUser = _maxNumberOfWithdrawalsPerUser;
}
```

## Proof of Concept
1. Deploy `KernelDepositPool` proxy; call `initialize(admin, kernelToken, rewardToken)`.
2. Confirm: `withdrawalDelay == 0`, `maxNumberOfWithdrawalsPerUser == 0`.
3. Alice calls `stake(1000e18)` → succeeds; `balanceOf[Alice] == 1000e18`.
4. Alice calls `initiateWithdrawal(1000e18)`:
   - L323: `userWithdrawalIds[Alice].length` is `0`; `maxNumberOfWithdrawalsPerUser` is `0`; `0 >= 0` → `revert WithdrawalLimitReached()`.
   - Alice's 1000 KERNEL are frozen.
5. Admin calls `setMaxNumberOfWithdrawalsPerUser(10)` (Effect 1 fixed; `withdrawalDelay` still `0`).
6. Alice calls `initiateWithdrawal(1000e18)` → succeeds; `unlockTime = block.timestamp + 0 = block.timestamp`.
7. In the same block, Alice calls `claimWithdrawal(1)`:
   - L355: `block.timestamp < block.timestamp` → `false` → passes.
   - Alice receives 1000 KERNEL with zero enforced delay.