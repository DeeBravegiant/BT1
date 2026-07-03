The code confirms the claim. Let me verify the key facts:

1. `initialize()` at [1](#0-0)  never assigns `maxNumberOfWithdrawalsPerUser`, leaving it at the Solidity default of `0`.

2. `initiateWithdrawal()` at [2](#0-1)  checks `userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser`. With `maxNumberOfWithdrawalsPerUser == 0`, this evaluates to `length >= 0`, which is always `true` for a `uint256`, causing every call to revert.

3. `setMaxNumberOfWithdrawalsPerUser()` at [3](#0-2)  explicitly rejects `0`, so the only resolution is an admin calling this setter with a valid value.

---

Audit Report

## Title
`initiateWithdrawal()` Always Reverts Due to Uninitialized `maxNumberOfWithdrawalsPerUser` — (File: contracts/KERNEL/KernelDepositPool.sol)

## Summary
`KernelDepositPool.initialize()` never sets `maxNumberOfWithdrawalsPerUser`, leaving it at its Solidity default of `0`. The guard in `initiateWithdrawal()` checks `userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser`, which evaluates to `uint256 >= 0` — always true — causing every withdrawal attempt to revert with `WithdrawalLimitReached`. All stakers are blocked from unstaking until an admin manually calls `setMaxNumberOfWithdrawalsPerUser()`.

## Finding Description
`initialize()` (L259–271) sets `kernelToken`, `rewardsToken`, and the admin role, but never assigns `maxNumberOfWithdrawalsPerUser`. Its value remains `0` post-deployment.

`initiateWithdrawal()` (L323) contains:
```solidity
if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();
```
Because `userWithdrawalIds[msg.sender].length` is a `uint256` (always `>= 0`) and `maxNumberOfWithdrawalsPerUser == 0`, the condition is unconditionally `true`. Every call reverts before any state is modified.

The setter `setMaxNumberOfWithdrawalsPerUser()` (L614) rejects `0` as an input, so the zero state cannot be restored once fixed, but it also means the contract ships in a broken state that only an admin action can repair. `stake()` has no dependency on this variable, so the contract accepts deposits normally while silently blocking all withdrawals.

## Impact Explanation
**Medium — Temporary freezing of funds.** Any user who has called `stake()` or `stakeFor()` cannot recover their KERNEL tokens until an admin calls `setMaxNumberOfWithdrawalsPerUser()`. The freeze is temporary (admin-recoverable) but affects all stakers simultaneously and is triggered by a normal unprivileged user action (`initiateWithdrawal()`).

## Likelihood Explanation
The missing initialization is not enforced or documented in `initialize()`. A deployer who follows the function signature alone will produce a contract that accepts stakes but blocks all withdrawals. No attacker action is required — any staker attempting to withdraw triggers the revert. The condition is deterministic and repeatable on every call.

## Recommendation
Initialize `maxNumberOfWithdrawalsPerUser` to a safe non-zero default inside `initialize()`:
```solidity
maxNumberOfWithdrawalsPerUser = MAX_WITHDRAWALS_PER_USER; // 100
```
This mirrors the pattern used for `withdrawalDelayBlocks` in `LRTWithdrawalManager.initialize()`.

## Proof of Concept
1. Deploy `KernelDepositPool` without calling `setMaxNumberOfWithdrawalsPerUser`.
2. Alice calls `stake(100e18)` — succeeds; `balanceOf[Alice] = 100e18`.
3. Alice calls `initiateWithdrawal(100e18)`.
4. Execution reaches L323:
   ```solidity
   if (userWithdrawalIds[Alice].length >= maxNumberOfWithdrawalsPerUser)
   //  0 >= 0  → true
       revert WithdrawalLimitReached();
   ```
5. Transaction reverts. Alice's tokens are locked until admin intervenes.

**Foundry test sketch:**
```solidity
function test_initiateWithdrawal_revertsWhenUninitialized() public {
    // Deploy without calling setMaxNumberOfWithdrawalsPerUser
    kernelToken.approve(address(pool), 100e18);
    pool.stake(100e18);
    vm.expectRevert(KernelDepositPool.WithdrawalLimitReached.selector);
    pool.initiateWithdrawal(100e18);
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

**File:** contracts/KERNEL/KernelDepositPool.sol (L323-323)
```text
        if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L614-615)
```text
        if (_maxNumberOfWithdrawalsPerUser == 0 || _maxNumberOfWithdrawalsPerUser > MAX_WITHDRAWALS_PER_USER) {
            revert InvalidMaxNumberOfWithdrawalsPerUser();
```
