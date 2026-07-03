Audit Report

## Title
`initiateWithdrawal()` permanently reverts post-deployment due to uninitialized `maxNumberOfWithdrawalsPerUser` — (`contracts/KERNEL/KernelDepositPool.sol`)

## Summary

`KernelDepositPool.initialize()` never assigns `maxNumberOfWithdrawalsPerUser`, leaving it at the Solidity default of `0`. The guard in `initiateWithdrawal()` evaluates `userWithdrawalIds[msg.sender].length >= 0`, which is always `true` for every caller, causing every withdrawal initiation to revert with `WithdrawalLimitReached` from the moment of deployment until an admin explicitly calls `setMaxNumberOfWithdrawalsPerUser`. All staked KERNEL tokens are temporarily unwithdrawable during this window.

## Finding Description

`initialize()` sets `kernelToken`, `rewardsToken`, and the admin role, but never assigns `maxNumberOfWithdrawalsPerUser`:

```solidity
// contracts/KERNEL/KernelDepositPool.sol L259-271
function initialize(address _admin, address _kernelToken, address _rewardToken) external initializer {
    ...
    kernelToken = IERC20(_kernelToken);
    rewardsToken = IERC20(_rewardToken);
    // maxNumberOfWithdrawalsPerUser left at default 0
}
``` [1](#0-0) 

`initiateWithdrawal()` then checks:

```solidity
// contracts/KERNEL/KernelDepositPool.sol L323
if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();
``` [2](#0-1) 

Because `maxNumberOfWithdrawalsPerUser == 0`, the condition `uint256_length >= 0` is unconditionally `true` for every caller (a `uint256` is always `≥ 0`). The function reverts before any state changes occur.

The only remedy is the admin calling `setMaxNumberOfWithdrawalsPerUser`, which itself rejects `0` as an argument: [3](#0-2) 

This setter is never invoked during initialization, so the contract is deployed in a state where the entire withdrawal lifecycle (`initiateWithdrawal` → `claimWithdrawal`) is blocked for all users.

## Impact Explanation

Every staker's KERNEL tokens are temporarily frozen: `initiateWithdrawal` reverts unconditionally, and `claimWithdrawal` requires a prior `initiateWithdrawal` record. No user can begin the exit process until the admin manually configures the parameter. This is a concrete, deterministic **temporary freezing of funds** (Medium) affecting all stakers from deployment.

## Likelihood Explanation

The condition is triggered on every deployment before the admin calls `setMaxNumberOfWithdrawalsPerUser`. No special attacker action is required — any staker calling `initiateWithdrawal` hits it deterministically. The window between deployment and the admin's configuration call is the exposure period, and any tokens staked during that window are locked.

## Recommendation

Set `maxNumberOfWithdrawalsPerUser` to a sensible non-zero default inside `initialize`:

```solidity
function initialize(address _admin, address _kernelToken, address _rewardToken) external initializer {
    ...
    kernelToken = IERC20(_kernelToken);
    rewardsToken = IERC20(_rewardToken);
+   maxNumberOfWithdrawalsPerUser = 10; // any value in (0, MAX_WITHDRAWALS_PER_USER]
}
```

This ensures the contract is functional from the moment of deployment without requiring a post-deployment admin call.

## Proof of Concept

1. Deploy `KernelDepositPool` via proxy and call `initialize(admin, kernelToken, rewardToken)`.
2. Confirm: `maxNumberOfWithdrawalsPerUser == 0`.
3. User calls `stake(1e18)` — succeeds; `balanceOf[user] == 1e18`.
4. User calls `initiateWithdrawal(1e18)`.
5. Execution reaches L323: `userWithdrawalIds[user].length >= maxNumberOfWithdrawalsPerUser` → `0 >= 0` → `true`.
6. Transaction reverts with `WithdrawalLimitReached`.
7. User's staked KERNEL is locked with no exit path.

Foundry test sketch:
```solidity
function test_withdrawalBlockedAtDeployment() public {
    // deploy proxy, call initialize
    vm.prank(user);
    pool.stake(1e18);
    vm.prank(user);
    vm.expectRevert(KernelDepositPool.WithdrawalLimitReached.selector);
    pool.initiateWithdrawal(1e18);
}
```

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L259-271)
```text
    function initialize(address _admin, address _kernelToken, address _rewardToken) external initializer {
        UtilLib.checkNonZeroAddress(_admin);
        UtilLib.checkNonZeroAddress(_kernelToken);
        UtilLib.checkNonZeroAddress(_rewardToken);

        __AccessControl_init();
        __ReentrancyGuard_init();

        _setupRole(DEFAULT_ADMIN_ROLE, _admin);

        kernelToken = IERC20(_kernelToken);
        rewardsToken = IERC20(_rewardToken);
    }
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L320-323)
```text
    function initiateWithdrawal(uint256 _amount) external nonReentrant updateReward(msg.sender) {
        if (_amount == 0) revert AmountZero();
        if (balanceOf[msg.sender] < _amount) revert InsufficientStakedBalance();
        if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L610-620)
```text
    function setMaxNumberOfWithdrawalsPerUser(uint256 _maxNumberOfWithdrawalsPerUser)
        external
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        if (_maxNumberOfWithdrawalsPerUser == 0 || _maxNumberOfWithdrawalsPerUser > MAX_WITHDRAWALS_PER_USER) {
            revert InvalidMaxNumberOfWithdrawalsPerUser();
        }

        maxNumberOfWithdrawalsPerUser = _maxNumberOfWithdrawalsPerUser;
        emit MaxNumberOfWithdrawalsPerUserUpdated(_maxNumberOfWithdrawalsPerUser);
    }
```
