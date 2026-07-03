### Title
Missing Initialization of `maxNumberOfWithdrawalsPerUser` in `KernelDepositPool.initialize()` Causes Temporary Freezing of Staked KERNEL Funds - (File: contracts/KERNEL/KernelDepositPool.sol)

---

### Summary

`KernelDepositPool.initialize()` does not set `maxNumberOfWithdrawalsPerUser`, leaving it at the Solidity default of `0`. Because `initiateWithdrawal()` unconditionally reverts when `userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser`, and any `uint256 >= 0` is always true, **no user can ever initiate a withdrawal** until the admin separately calls `setMaxNumberOfWithdrawalsPerUser()`. This is a direct analog to the Zerem M-05 class: a critical configuration parameter is left at an invalid default in the initializer, causing user funds to be temporarily frozen.

---

### Finding Description

`KernelDepositPool.initialize()` only validates and sets three parameters — `_admin`, `_kernelToken`, and `_rewardToken` — and does not initialize `maxNumberOfWithdrawalsPerUser` or `withdrawalDelay`. [1](#0-0) 

`maxNumberOfWithdrawalsPerUser` therefore defaults to `0`. The `initiateWithdrawal()` function contains the following guard: [2](#0-1) 

Because `userWithdrawalIds[msg.sender].length` is a `uint256` (always `>= 0`) and `maxNumberOfWithdrawalsPerUser == 0`, the condition `length >= 0` is always `true`. Every call to `initiateWithdrawal()` reverts with `WithdrawalLimitReached()` regardless of how much KERNEL the user has staked.

The setter that can fix this has proper bounds: [3](#0-2) 

However, the setter is never called from `initialize()`, so the contract is deployed in a broken state. Users who call `stake()` or `stakeFor()` immediately after deployment have their KERNEL tokens locked with no path to withdrawal until the admin intervenes.

---

### Impact Explanation

All staked KERNEL tokens are temporarily frozen from the moment of deployment until the admin calls `setMaxNumberOfWithdrawalsPerUser()` with a valid value. During this window — which could be hours, days, or indefinitely if the admin is unaware — users cannot initiate withdrawals of their staked KERNEL. This matches **Medium — Temporary freezing of funds**.

---

### Likelihood Explanation

The deployment of `KernelDepositPool` requires a separate post-deployment admin call to configure `maxNumberOfWithdrawalsPerUser`. If the deployment script omits this step (a realistic fat-finger or script error, exactly as described in the Zerem M-05 report), the contract is silently misconfigured. The broken state is not immediately obvious because `stake()` and `stakeFor()` succeed normally; only withdrawal attempts reveal the issue. Likelihood is **Medium**.

---

### Recommendation

Set a sensible default for `maxNumberOfWithdrawalsPerUser` (and `withdrawalDelay`) directly inside `initialize()`, or add a non-zero require check so the initializer reverts if these are not provided:

```solidity
function initialize(
    address _admin,
    address _kernelToken,
    address _rewardToken,
    uint256 _maxNumberOfWithdrawalsPerUser,
    uint256 _withdrawalDelay
) external initializer {
    // existing checks ...
    if (_maxNumberOfWithdrawalsPerUser == 0 || _maxNumberOfWithdrawalsPerUser > MAX_WITHDRAWALS_PER_USER)
        revert InvalidMaxNumberOfWithdrawalsPerUser();
    if (_withdrawalDelay == 0 || _withdrawalDelay > MAX_WITHDRAWAL_DELAY)
        revert InvalidWithdrawalDelay();

    maxNumberOfWithdrawalsPerUser = _maxNumberOfWithdrawalsPerUser;
    withdrawalDelay = _withdrawalDelay;
    // ...
}
```

---

### Proof of Concept

1. Admin deploys `KernelDepositPool` proxy and calls `initialize(admin, kernelToken, rewardToken)`. `maxNumberOfWithdrawalsPerUser` is `0`.
2. User calls `stake(1e18)`. Succeeds. User's KERNEL is now held by the contract.
3. User calls `initiateWithdrawal(1e18)`.
4. The check `userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser` evaluates to `0 >= 0 == true`.
5. Transaction reverts with `WithdrawalLimitReached()`.
6. User's KERNEL is frozen until admin calls `setMaxNumberOfWithdrawalsPerUser(N)`. [4](#0-3)

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L320-337)
```text
    function initiateWithdrawal(uint256 _amount) external nonReentrant updateReward(msg.sender) {
        if (_amount == 0) revert AmountZero();
        if (balanceOf[msg.sender] < _amount) revert InsufficientStakedBalance();
        if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();

        balanceOf[msg.sender] -= _amount;
        totalKernelStaked -= _amount;

        // Create a withdrawal record
        uint256 withdrawalId = ++withdrawalCounter;
        uint256 unlockTime = block.timestamp + withdrawalDelay;

        withdrawals[withdrawalId] = Withdrawal({
            user: msg.sender, amount: _amount, unlockTime: unlockTime, claimed: false, withdrawalId: withdrawalId
        });
        userWithdrawalIds[msg.sender].push(withdrawalId);

        emit WithdrawalInitiated(msg.sender, _amount, withdrawalId, unlockTime);
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
