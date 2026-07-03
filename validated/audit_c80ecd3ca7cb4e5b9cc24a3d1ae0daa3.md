### Title
Uninitialized `maxNumberOfWithdrawalsPerUser` Causes Permanent DoS on `initiateWithdrawal()` Until Admin Intervention - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
`KernelDepositPool.initialize()` never sets `maxNumberOfWithdrawalsPerUser`, leaving it at the Solidity default of `0`. The guard in `initiateWithdrawal()` immediately evaluates `0 >= 0 → true` for every caller, reverting with `WithdrawalLimitReached()`. Any user who has staked KERNEL tokens is unable to initiate a withdrawal until an admin manually calls `setMaxNumberOfWithdrawalsPerUser()`.

### Finding Description
`KernelDepositPool.initialize()` configures roles and token addresses but omits any assignment to `maxNumberOfWithdrawalsPerUser`:

```solidity
// contracts/KERNEL/KernelDepositPool.sol  lines 259-271
function initialize(address _admin, address _kernelToken, address _rewardToken) external initializer {
    ...
    kernelToken = IERC20(_kernelToken);
    rewardsToken = IERC20(_rewardToken);
    // maxNumberOfWithdrawalsPerUser is never set → defaults to 0
}
```

`initiateWithdrawal()` enforces the limit before pushing to the per-user array:

```solidity
// line 323
if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();
```

For every user, `userWithdrawalIds[msg.sender].length` starts at `0`. With `maxNumberOfWithdrawalsPerUser == 0`, the condition `0 >= 0` is always `true`, so every call to `initiateWithdrawal()` reverts. The only recovery path is an admin calling `setMaxNumberOfWithdrawalsPerUser()` with a non-zero value (capped at the constant `MAX_WITHDRAWALS_PER_USER = 100`).

The analog to the original report is direct: the original bug left an array **unbounded** (no limit at all), causing gas-exhaustion DoS. Here the limit exists in code but is **never initialized**, producing an equally effective DoS — the array can never grow past zero entries, so the withdrawal path is completely blocked from the moment of deployment.

### Impact Explanation
All users who have staked KERNEL tokens via `stake()` or `stakeFor()` are unable to initiate withdrawals. Their principal is locked in the contract until an admin intervenes. This constitutes **temporary freezing of funds** (Medium impact per the allowed scope).

### Likelihood Explanation
The condition is triggered unconditionally on every call to `initiateWithdrawal()` immediately after deployment. No special attacker setup is required — any ordinary staker attempting to exit will hit the revert. The likelihood is high.

### Recommendation
Set a safe default in `initialize()`:

```solidity
maxNumberOfWithdrawalsPerUser = MAX_WITHDRAWALS_PER_USER; // 100
```

Alternatively, add a `require(maxNumberOfWithdrawalsPerUser > 0, ...)` guard in `initiateWithdrawal()` with a descriptive error, and enforce initialization in the constructor/initializer.

### Proof of Concept
1. Deploy `KernelDepositPool` and call `initialize(admin, kernelToken, rewardToken)`.
2. Confirm `maxNumberOfWithdrawalsPerUser == 0` (never set).
3. User calls `stake(1e18)` — succeeds; `balanceOf[user] = 1e18`.
4. User calls `initiateWithdrawal(1e18)`.
5. Execution reaches line 323: `userWithdrawalIds[user].length (0) >= maxNumberOfWithdrawalsPerUser (0)` → `true` → reverts `WithdrawalLimitReached()`.
6. User's `1e18` KERNEL is locked; `claimWithdrawal()` is unreachable because no withdrawal record is ever created.
7. Funds remain frozen until admin calls `setMaxNumberOfWithdrawalsPerUser(N)` where `0 < N ≤ 100`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L38-38)
```text
    uint256 public constant MAX_WITHDRAWALS_PER_USER = 100;
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
