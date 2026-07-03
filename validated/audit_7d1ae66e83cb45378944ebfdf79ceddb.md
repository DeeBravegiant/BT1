Audit Report

## Title
Uninitialized `maxNumberOfWithdrawalsPerUser` Blocks All Withdrawals Until Admin Intervenes — (`contracts/KERNEL/KernelDepositPool.sol`)

## Summary
`maxNumberOfWithdrawalsPerUser` is declared as a plain storage variable defaulting to `0` and is never assigned in `initialize()`. The guard in `initiateWithdrawal` evaluates `0 >= 0 == true` on every call, causing an unconditional `WithdrawalLimitReached` revert for all users. Funds staked before the admin calls `setMaxNumberOfWithdrawalsPerUser` are frozen with no alternative withdrawal path.

## Finding Description
`maxNumberOfWithdrawalsPerUser` is declared at L108 as a plain `uint256` storage variable, defaulting to `0`: [1](#0-0) 

`initialize()` sets `kernelToken`, `rewardsToken`, and roles, but never assigns `maxNumberOfWithdrawalsPerUser`: [2](#0-1) 

`initiateWithdrawal` checks at L323:
```solidity
if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();
``` [3](#0-2) 

For any user who has never initiated a withdrawal, `userWithdrawalIds[msg.sender].length == 0`. With `maxNumberOfWithdrawalsPerUser == 0`, the condition is `0 >= 0 == true`, so the function always reverts before any state mutation. `stake()` succeeds normally (L281–289), but the only exit path for staked principal is blocked.

The constant `MAX_WITHDRAWALS_PER_USER = 100` exists at L38, indicating the intended default, but it was never wired into `initialize()`: [4](#0-3) 

The admin-only setter at L610–620 can unblock withdrawals, but it is not called during initialization and there is no on-chain guard preventing users from staking before it is set: [5](#0-4) 

## Impact Explanation
**Medium — Temporary freezing of funds.** Any user who calls `stake()` before the admin calls `setMaxNumberOfWithdrawalsPerUser` has their principal frozen with no withdrawal path. The freeze persists until the admin acts. Because the admin setter exists and is callable, the freeze is not provably permanent (which would require the admin to never act), placing this squarely in the temporary-freeze category. The staked principal is at risk during the entire deployment-to-configuration gap.

## Likelihood Explanation
The contract is upgradeable and post-deploy configuration is expected, but `maxNumberOfWithdrawalsPerUser` is not documented as a required step and there is no on-chain enforcement preventing users from staking before it is set. Any user who stakes during the configuration gap — which may span blocks, hours, or longer — is immediately locked out of withdrawals. The bug is triggered by normal, unprivileged `stake()` + `initiateWithdrawal()` calls with no special preconditions.

## Recommendation
Set a safe default in `initialize()`:
```solidity
maxNumberOfWithdrawalsPerUser = MAX_WITHDRAWALS_PER_USER; // 100
```
Alternatively, add a guard in `stake()` to prevent deposits before the parameter is configured:
```solidity
require(maxNumberOfWithdrawalsPerUser > 0, "withdrawals not configured");
```

## Proof of Concept
```solidity
function testTemporaryWithdrawalFreeze() public {
    // Deploy proxy + initialize WITHOUT calling setMaxNumberOfWithdrawalsPerUser
    KernelDepositPool pool = deployAndInitialize(admin, address(kernelToken), address(rewardToken));

    // Confirm the parameter is 0
    assertEq(pool.maxNumberOfWithdrawalsPerUser(), 0);

    // User stakes successfully
    vm.startPrank(user);
    kernelToken.approve(address(pool), 1e18);
    pool.stake(1e18);
    assertEq(pool.balanceOf(user), 1e18);

    // User cannot withdraw — 0 >= 0 is always true
    vm.expectRevert(KernelDepositPool.WithdrawalLimitReached.selector);
    pool.initiateWithdrawal(1e18);

    // Funds remain locked; no alternative withdrawal path exists
    assertEq(pool.balanceOf(user), 1e18);
    vm.stopPrank();

    // Admin unblocks by calling the setter
    vm.prank(admin);
    pool.setMaxNumberOfWithdrawalsPerUser(100);

    // Now withdrawal succeeds
    vm.prank(user);
    pool.initiateWithdrawal(1e18); // no revert
}
```

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L38-38)
```text
    uint256 public constant MAX_WITHDRAWALS_PER_USER = 100;
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L107-108)
```text
    /// @notice The maximum number of withdrawals that any user can have open (unclaimed) at any time
    uint256 public maxNumberOfWithdrawalsPerUser;
```

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L610-619)
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
```
