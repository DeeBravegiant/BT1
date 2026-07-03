Audit Report

## Title
Uninitialized `withdrawalDelay` and `maxNumberOfWithdrawalsPerUser` in `initialize()` Cause Withdrawal Freeze and Delay Bypass - (File: contracts/KERNEL/KernelDepositPool.sol)

## Summary
`KernelDepositPool.initialize()` never assigns `withdrawalDelay` or `maxNumberOfWithdrawalsPerUser`, leaving both at Solidity's default of `0`. Because `initiateWithdrawal()` guards with `userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser`, a value of `0` makes the condition `0 >= 0` unconditionally true, causing every withdrawal attempt to revert with `WithdrawalLimitReached()` and temporarily freezing all staked KERNEL tokens. Separately, `withdrawalDelay = 0` sets `unlockTime = block.timestamp`, making withdrawals immediately claimable and bypassing the intended time-lock.

## Finding Description
`initialize()` sets only `kernelToken`, `rewardsToken`, and `DEFAULT_ADMIN_ROLE`: [1](#0-0) 

Both `withdrawalDelay` and `maxNumberOfWithdrawalsPerUser` are declared but never assigned: [2](#0-1) 

**Freeze path:** `initiateWithdrawal()` checks: [3](#0-2) 

With `maxNumberOfWithdrawalsPerUser = 0`, `userWithdrawalIds[msg.sender].length` (a `uint256`) is always `>= 0`, so the revert fires unconditionally for every caller.

**Zero-delay path:** The unlock time is computed as: [4](#0-3) 

With `withdrawalDelay = 0`, `unlockTime = block.timestamp`. The claim guard `block.timestamp < withdrawal.unlockTime` is immediately false, so withdrawals are claimable in the same block they are initiated.

The setter functions correctly reject `0`, but they are not called during initialization: [5](#0-4) [6](#0-5) 

There is no on-chain mechanism that enforces calling these setters after deployment.

## Impact Explanation
**Primary — Medium. Temporary freezing of funds:** Every user who has staked KERNEL via `stake()` or `stakeFor()` is completely unable to initiate a withdrawal from the moment of deployment until the admin separately calls `setMaxNumberOfWithdrawalsPerUser()`. The staked balance is locked in the contract with no user-accessible recourse.

**Secondary — Low. Contract fails to deliver promised returns:** With `withdrawalDelay = 0`, the time-lock guarantee is absent; users can stake and claim in the same block once the freeze is lifted, defeating the protocol's stated withdrawal delay protection.

## Likelihood Explanation
Likelihood is high for the freeze. `initialize()` is the sole initialization path for the upgradeable proxy. There is no on-chain enforcement requiring a post-deployment call to `setMaxNumberOfWithdrawalsPerUser()`. Any deployment that omits this follow-up call — whether by oversight, scripting error, or incomplete deployment runbook — leaves the contract in a broken state immediately. The condition is triggered by any ordinary user calling `initiateWithdrawal()` after staking, requiring no special privileges or unusual assumptions.

## Recommendation
1. Initialize both variables to safe non-zero defaults inside `initialize()`:
   ```solidity
   withdrawalDelay = 7 days;
   maxNumberOfWithdrawalsPerUser = 10;
   ```
2. Add a minimum threshold check in `setWithdrawalDelay()` (e.g., `>= 1 hours`) analogous to the existing `MAX_WITHDRAWAL_DELAY` upper bound.
3. Emit initialization events for off-chain monitoring to confirm correct deployment state.

## Proof of Concept
```
1. Deploy KernelDepositPool proxy; call initialize(admin, kernelToken, rewardToken).
   → withdrawalDelay = 0, maxNumberOfWithdrawalsPerUser = 0 (Solidity defaults)

2. Admin does NOT call setMaxNumberOfWithdrawalsPerUser() (no on-chain enforcement).

3. User calls stake(1e18) → succeeds; KERNEL transferred in, balanceOf[user] = 1e18.

4. User calls initiateWithdrawal(1e18):
   - userWithdrawalIds[user].length = 0
   - maxNumberOfWithdrawalsPerUser = 0
   - 0 >= 0 → true → revert WithdrawalLimitReached()

5. User's 1e18 KERNEL is frozen in the contract.
   No user-callable function can recover it until admin calls setMaxNumberOfWithdrawalsPerUser(n > 0).

Foundry test sketch:
  function test_freezeOnDeploy() public {
      pool.initialize(admin, address(kernel), address(reward));
      kernel.mint(user, 1e18);
      vm.prank(user); kernel.approve(address(pool), 1e18);
      vm.prank(user); pool.stake(1e18);
      vm.prank(user);
      vm.expectRevert(KernelDepositPool.WithdrawalLimitReached.selector);
      pool.initiateWithdrawal(1e18);
  }
```

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L96-108)
```text
    uint256 public withdrawalDelay;

    /// @notice A global incremental counter for withdrawal IDs
    uint256 public withdrawalCounter;

    /// @notice Mapping of withdrawal IDs to their withdrawal info
    mapping(uint256 withdrawalId => Withdrawal withdrawal) public withdrawals;

    /// @notice Mapping of user addresses to their withdrawal IDs
    mapping(address user => uint256[] withdrawalIds) public userWithdrawalIds;

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L330-330)
```text
        uint256 unlockTime = block.timestamp + withdrawalDelay;
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L598-600)
```text
    function setWithdrawalDelay(uint256 _withdrawalDelay) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_withdrawalDelay == 0) revert InvalidWithdrawalDelay();
        if (_withdrawalDelay > MAX_WITHDRAWAL_DELAY) revert MaximumWithdrawalDelayExceeded();
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L614-615)
```text
        if (_maxNumberOfWithdrawalsPerUser == 0 || _maxNumberOfWithdrawalsPerUser > MAX_WITHDRAWALS_PER_USER) {
            revert InvalidMaxNumberOfWithdrawalsPerUser();
```
