Audit Report

## Title
`maxNumberOfWithdrawalsPerUser` Uninitialized in `initialize()` Permanently Blocks `initiateWithdrawal()` Until Admin Intervenes - (File: contracts/KERNEL/KernelDepositPool.sol)

## Summary
`KernelDepositPool.initialize()` never assigns `maxNumberOfWithdrawalsPerUser`, leaving it at the Solidity default of `0`. Because `initiateWithdrawal()` enforces `userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser`, the condition `0 >= 0` is always true immediately after deployment, causing every withdrawal attempt to revert with `WithdrawalLimitReached`. All staked KERNEL tokens are frozen until an admin manually calls `setMaxNumberOfWithdrawalsPerUser()`.

## Finding Description
`maxNumberOfWithdrawalsPerUser` is declared as a plain storage variable with no default assignment: [1](#0-0) 

`initialize()` sets only `kernelToken` and `rewardsToken`, never touching `maxNumberOfWithdrawalsPerUser`: [2](#0-1) 

`initiateWithdrawal()` enforces the limit at line 323: [3](#0-2) 

With `maxNumberOfWithdrawalsPerUser == 0`, the expression `userWithdrawalIds[msg.sender].length >= 0` evaluates to `true` for every caller (including a fresh user with zero pending withdrawals), causing an unconditional revert. The admin setter explicitly rejects `0` as invalid, confirming `0` is not a valid operational state: [4](#0-3) 

No deployment scripts or other contracts call `setMaxNumberOfWithdrawalsPerUser()` post-initialization; the only occurrences of the variable are within `KernelDepositPool.sol` itself.

## Impact Explanation
Any user who calls `stake()` or is staked via `stakeFor()` cannot initiate a withdrawal until an admin intervenes. The staked KERNEL tokens are locked in the contract with no user-accessible exit path. This is a concrete **Medium — Temporary freezing of funds**: funds are not permanently lost (admin can unblock), but users are unable to begin the unlock process at all during the window between deployment and admin remediation.

## Likelihood Explanation
The broken state is the deterministic default on every fresh deployment. No attacker action, special conditions, or external dependencies are required — any user who stakes and then calls `initiateWithdrawal()` will hit the revert. The likelihood is **High**.

## Recommendation
Set `maxNumberOfWithdrawalsPerUser` to a valid non-zero value inside `initialize()`:

```solidity
function initialize(address _admin, address _kernelToken, address _rewardToken) external initializer {
    UtilLib.checkNonZeroAddress(_admin);
    UtilLib.checkNonZeroAddress(_kernelToken);
    UtilLib.checkNonZeroAddress(_rewardToken);

    __AccessControl_init();
    __ReentrancyGuard_init();

    _setupRole(DEFAULT_ADMIN_ROLE, _admin);

    kernelToken = IERC20(_kernelToken);
    rewardsToken = IERC20(_rewardToken);
    maxNumberOfWithdrawalsPerUser = MAX_WITHDRAWALS_PER_USER; // add this line
}
```

## Proof of Concept
1. Deploy `KernelDepositPool` proxy and call `initialize(admin, kernelToken, rewardToken)`.
2. Confirm `maxNumberOfWithdrawalsPerUser == 0` (never set).
3. Call `stake(100e18)` as any user — succeeds, KERNEL tokens transferred in.
4. Call `initiateWithdrawal(100e18)` as the same user:
   - `userWithdrawalIds[user].length` is `0`.
   - Check: `0 >= 0` → `true` → reverts with `WithdrawalLimitReached`.
5. User's KERNEL tokens remain locked; no withdrawal path exists until admin calls `setMaxNumberOfWithdrawalsPerUser(N)` where `N > 0`.

Foundry test sketch:
```solidity
function test_withdrawalBlockedOnInit() public {
    // deploy and initialize without calling setMaxNumberOfWithdrawalsPerUser
    pool.initialize(admin, address(kernelToken), address(rewardToken));
    kernelToken.approve(address(pool), 100e18);
    pool.stake(100e18);
    vm.expectRevert(KernelDepositPool.WithdrawalLimitReached.selector);
    pool.initiateWithdrawal(100e18);
}
```

### Citations

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L320-323)
```text
    function initiateWithdrawal(uint256 _amount) external nonReentrant updateReward(msg.sender) {
        if (_amount == 0) revert AmountZero();
        if (balanceOf[msg.sender] < _amount) revert InsufficientStakedBalance();
        if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L610-616)
```text
    function setMaxNumberOfWithdrawalsPerUser(uint256 _maxNumberOfWithdrawalsPerUser)
        external
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        if (_maxNumberOfWithdrawalsPerUser == 0 || _maxNumberOfWithdrawalsPerUser > MAX_WITHDRAWALS_PER_USER) {
            revert InvalidMaxNumberOfWithdrawalsPerUser();
        }
```
