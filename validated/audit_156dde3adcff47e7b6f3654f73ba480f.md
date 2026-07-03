### Title
`maxNumberOfWithdrawalsPerUser` Uninitialized in `KernelDepositPool.initialize()`, Permanently Blocking All Withdrawals Until Admin Intervenes - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
`KernelDepositPool.initialize()` never sets `maxNumberOfWithdrawalsPerUser`, leaving it at the Solidity default of `0`. The setter `setMaxNumberOfWithdrawalsPerUser()` explicitly rejects `0` as invalid. Because `initiateWithdrawal()` guards with `userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser`, and any `uint256` array length is always `>= 0`, every call to `initiateWithdrawal()` reverts with `WithdrawalLimitReached` from the moment of deployment until an admin manually calls `setMaxNumberOfWithdrawalsPerUser`.

### Finding Description
`KernelDepositPool.initialize()` sets up roles and token addresses but never assigns `maxNumberOfWithdrawalsPerUser`: [1](#0-0) 

The variable therefore holds `0` (Solidity default). The setter explicitly treats `0` as an invalid value: [2](#0-1) 

`initiateWithdrawal()` enforces the limit before creating a withdrawal record: [3](#0-2) 

Because `userWithdrawalIds[msg.sender].length` (a `uint256`) is always `>= 0`, the condition `length >= 0` is unconditionally `true`, so the function always reverts with `WithdrawalLimitReached`.

### Impact Explanation
Every user who stakes KERNEL tokens via `stake()` or `stakeFor()` is immediately unable to initiate a withdrawal. Their tokens are locked in the contract until an admin calls `setMaxNumberOfWithdrawalsPerUser` with a non-zero value. This constitutes a **temporary freezing of user funds** — Medium severity under the allowed impact scope.

### Likelihood Explanation
The condition is triggered on the very first call to `initiateWithdrawal()` by any user, with no special preconditions. Any depositor who stakes tokens before the admin sets the variable will be unable to retrieve them. Likelihood is **High**.

### Recommendation
Initialize `maxNumberOfWithdrawalsPerUser` to a sensible default (e.g., `MAX_WITHDRAWALS_PER_USER`) inside `initialize()`:

```solidity
function initialize(address _admin, address _kernelToken, address _rewardToken) external initializer {
    // ... existing setup ...
    maxNumberOfWithdrawalsPerUser = MAX_WITHDRAWALS_PER_USER;
}
```

This mirrors the pattern used in `LRTWithdrawalManager.initialize()`, which correctly sets `withdrawalDelayBlocks` to a concrete value at construction time: [4](#0-3) 

### Proof of Concept

1. Admin deploys and calls `KernelDepositPool.initialize(admin, kernelToken, rewardToken)`.
2. `maxNumberOfWithdrawalsPerUser` is `0` (never set).
3. User calls `stake(1e18)` — succeeds; `balanceOf[user] = 1e18`.
4. User calls `initiateWithdrawal(1e18)`.
5. Check: `userWithdrawalIds[user].length (= 0) >= maxNumberOfWithdrawalsPerUser (= 0)` → `true`.
6. Transaction reverts with `WithdrawalLimitReached`.
7. User's `1e18` KERNEL tokens remain locked in the contract with no withdrawal path available. [5](#0-4) [6](#0-5)

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

**File:** contracts/LRTWithdrawalManager.sol (L90-98)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        __Pausable_init();
        __ReentrancyGuard_init();
        withdrawalDelayBlocks = 8 days / 12 seconds;

        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
```
