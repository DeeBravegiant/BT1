Audit Report

## Title
Missing initialization of `maxNumberOfWithdrawalsPerUser` causes DoS on `initiateWithdrawal()` before admin configuration - (File: contracts/KERNEL/KernelDepositPool.sol)

## Summary
The `initialize()` function in `KernelDepositPool` never assigns a default value to `maxNumberOfWithdrawalsPerUser`, leaving it at Solidity's default of `0`. Because `initiateWithdrawal()` guards with `userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser`, the condition evaluates to `0 >= 0 = true` for every caller, causing every withdrawal attempt to revert with `WithdrawalLimitReached` until the admin explicitly calls `setMaxNumberOfWithdrawalsPerUser`. Any user who stakes before that admin call has their tokens frozen with no withdrawal path.

## Finding Description
`initialize()` sets only `kernelToken` and `rewardsToken` and leaves `maxNumberOfWithdrawalsPerUser` at `0`:

```solidity
// L259-271
function initialize(address _admin, address _kernelToken, address _rewardToken) external initializer {
    ...
    kernelToken = IERC20(_kernelToken);
    rewardsToken = IERC20(_rewardToken);
    // maxNumberOfWithdrawalsPerUser is never set — remains 0
}
```

`initiateWithdrawal()` at L323 checks:

```solidity
if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();
```

With `maxNumberOfWithdrawalsPerUser == 0`, a fresh user has `userWithdrawalIds[msg.sender].length == 0`, so `0 >= 0` is `true` and the function always reverts. Meanwhile, `stake()` (L281-289) has no such dependency and succeeds freely, allowing users to lock tokens they cannot retrieve.

The only unblock path is the admin calling `setMaxNumberOfWithdrawalsPerUser` (L610-620), which is gated by `onlyRole(DEFAULT_ADMIN_ROLE)` and explicitly rejects `0` as an argument — meaning the initial state of `0` can never be set via the setter, only overwritten to a valid value.

The constant `MAX_WITHDRAWALS_PER_USER = 100` (L38) exists and would be a natural default, but is never referenced in `initialize()`.

## Impact Explanation
**Medium — Temporary freezing of funds.** Any user who stakes KERNEL tokens before the admin completes configuration has their tokens locked in the contract with no ability to initiate a withdrawal. The freeze is not permanent (admin can unblock), but the duration is indefinite and entirely at the admin's discretion. The staking path is fully open with no on-chain warning that withdrawals are blocked.

## Likelihood Explanation
**Medium.** The deployment sequence is: deploy proxy → `initialize()` → (optionally) `setWithdrawalDelay` + `setMaxNumberOfWithdrawalsPerUser` → open to users. There is no on-chain enforcement that the setter calls precede any user interaction. A user who stakes immediately after `initialize()` — before the admin completes configuration — will find their tokens frozen. This is a realistic race condition in any deployment where staking is opened before all parameters are configured, and requires no attacker: any ordinary user triggers it by calling `stake()` followed by `initiateWithdrawal()`.

## Recommendation
Initialize `maxNumberOfWithdrawalsPerUser` to the existing constant inside `initialize()`:

```solidity
function initialize(address _admin, address _kernelToken, address _rewardToken) external initializer {
    ...
    kernelToken = IERC20(_kernelToken);
    rewardsToken = IERC20(_rewardToken);
    maxNumberOfWithdrawalsPerUser = MAX_WITHDRAWALS_PER_USER; // add this line
}
```

Similarly, `withdrawalDelay` should be initialized to a non-zero default (e.g., `1 days`) to prevent zero-delay withdrawals if `setWithdrawalDelay` is never called.

## Proof of Concept
1. Deploy `KernelDepositPool` proxy and call `initialize(admin, kernelToken, rewardToken)`.
2. Admin does **not** call `setMaxNumberOfWithdrawalsPerUser` (incomplete deployment script or race condition).
3. User calls `stake(100e18)` — **succeeds**. `balanceOf[user] = 100e18`, `maxNumberOfWithdrawalsPerUser = 0`.
4. User calls `initiateWithdrawal(100e18)` — **reverts** with `WithdrawalLimitReached` because `userWithdrawalIds[user].length (0) >= maxNumberOfWithdrawalsPerUser (0)` evaluates to `true` at L323.
5. User's `100e18` KERNEL tokens are locked with no withdrawal path until admin calls `setMaxNumberOfWithdrawalsPerUser(N)`.

Foundry test sketch:
```solidity
function test_withdrawalDosBeforeAdminConfig() public {
    // deploy and initialize without calling setMaxNumberOfWithdrawalsPerUser
    pool.initialize(admin, address(kernelToken), address(rewardToken));
    kernelToken.mint(user, 100e18);
    vm.startPrank(user);
    kernelToken.approve(address(pool), 100e18);
    pool.stake(100e18); // succeeds
    vm.expectRevert(KernelDepositPool.WithdrawalLimitReached.selector);
    pool.initiateWithdrawal(100e18); // reverts
    vm.stopPrank();
}
```