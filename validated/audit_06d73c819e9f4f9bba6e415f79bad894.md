### Title
Missing initialization of `maxNumberOfWithdrawalsPerUser` causes permanent DoS on `initiateWithdrawal()` before admin configuration - (File: contracts/KERNEL/KernelDepositPool.sol)

---

### Summary

`KernelDepositPool.initiateWithdrawal()` assumes `maxNumberOfWithdrawalsPerUser` has been configured to a non-zero value, but the `initialize()` function never sets a default. Because Solidity initializes `uint256` storage to `0`, the guard `userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser` evaluates to `0 >= 0 = true` for every user, causing every call to `initiateWithdrawal()` to revert with `WithdrawalLimitReached`. Users who have already staked KERNEL tokens cannot withdraw them until the admin explicitly calls `setMaxNumberOfWithdrawalsPerUser`.

---

### Finding Description

`KernelDepositPool` is a staking contract where users deposit KERNEL tokens and later withdraw them via a two-step process: `initiateWithdrawal()` followed by `claimWithdrawal()` after a delay.

The `initialize()` function sets only `kernelToken` and `rewardsToken`:

```solidity
function initialize(address _admin, address _kernelToken, address _rewardToken) external initializer {
    ...
    kernelToken = IERC20(_kernelToken);
    rewardsToken = IERC20(_rewardToken);
}
```

Neither `withdrawalDelay` nor `maxNumberOfWithdrawalsPerUser` is assigned a default value here. Both remain `0` after initialization. [1](#0-0) 

Inside `initiateWithdrawal()`, the very first guard is:

```solidity
if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();
```

For any user who has never initiated a withdrawal, `userWithdrawalIds[msg.sender].length == 0`. With `maxNumberOfWithdrawalsPerUser == 0`, the condition `0 >= 0` is `true`, so the function always reverts. [2](#0-1) 

Meanwhile, `stake()` has no such dependency and succeeds freely:

```solidity
function stake(uint256 _amount) external nonReentrant updateReward(msg.sender) {
    if (_amount == 0) revert AmountZero();
    balanceOf[msg.sender] += _amount;
    totalKernelStaked += _amount;
    kernelToken.safeTransferFrom(msg.sender, address(this), _amount);
    emit Staked(msg.sender, _amount);
}
``` [3](#0-2) 

The setter `setMaxNumberOfWithdrawalsPerUser` is admin-only and is the only path to unblock withdrawals: [4](#0-3) 

This is the direct analog to the original report: just as `finishPrePurchasersMode()` assumed `lastUpdateTime > 0` without checking, `initiateWithdrawal()` assumes `maxNumberOfWithdrawalsPerUser > 0` without checking, causing incorrect behavior (a complete DoS on withdrawals) when the contract is used before that state variable is configured.

---

### Impact Explanation

**Medium — Temporary freezing of funds.**

Any user who stakes KERNEL tokens before the admin calls `setMaxNumberOfWithdrawalsPerUser` has their tokens locked in the contract with no ability to initiate a withdrawal. The tokens are not lost permanently (admin can unblock by calling the setter), but the user's funds are frozen for an indefinite period. The staking path is fully open to users with no warning that withdrawals are blocked.

---

### Likelihood Explanation

**Medium.**

The deployment sequence is: deploy proxy → call `initialize()` → (optionally) call `setWithdrawalDelay` and `setMaxNumberOfWithdrawalsPerUser` → open to users. There is no on-chain enforcement that the two setter calls happen before any user stakes. A user who stakes immediately after `initialize()` — before the admin completes configuration — will find their tokens frozen. This is a realistic race condition in any deployment where staking is opened before all parameters are set.

---

### Recommendation

Initialize `maxNumberOfWithdrawalsPerUser` to a sensible default (e.g., `MAX_WITHDRAWALS_PER_USER = 100`) inside `initialize()`:

```solidity
function initialize(address _admin, address _kernelToken, address _rewardToken) external initializer {
    ...
    maxNumberOfWithdrawalsPerUser = MAX_WITHDRAWALS_PER_USER;
}
```

Alternatively, add an explicit guard in `initiateWithdrawal()`:

```solidity
if (maxNumberOfWithdrawalsPerUser == 0) revert NotConfigured();
```

Similarly, `withdrawalDelay` should be initialized to a non-zero default to prevent zero-delay withdrawals if the admin forgets to call `setWithdrawalDelay`.

---

### Proof of Concept

1. Deploy `KernelDepositPool` and call `initialize(admin, kernelToken, rewardToken)`.
2. Admin does **not** yet call `setMaxNumberOfWithdrawalsPerUser` (e.g., deployment script is incomplete).
3. User calls `stake(100e18)` — **succeeds**. `balanceOf[user] = 100e18`.
4. User calls `initiateWithdrawal(100e18)` — **reverts** with `WithdrawalLimitReached` because `userWithdrawalIds[user].length (0) >= maxNumberOfWithdrawalsPerUser (0)` is `true`.
5. User's 100e18 KERNEL tokens are locked in the contract with no withdrawal path until admin calls `setMaxNumberOfWithdrawalsPerUser(N)`.

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L281-289)
```text
    function stake(uint256 _amount) external nonReentrant updateReward(msg.sender) {
        if (_amount == 0) revert AmountZero();

        balanceOf[msg.sender] += _amount;
        totalKernelStaked += _amount;
        kernelToken.safeTransferFrom(msg.sender, address(this), _amount);

        emit Staked(msg.sender, _amount);
    }
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L320-338)
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
    }
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
