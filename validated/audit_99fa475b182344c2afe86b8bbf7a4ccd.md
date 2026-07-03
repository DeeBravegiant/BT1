### Title
Uninitialized `maxNumberOfWithdrawalsPerUser` Blocks All Withdrawals, Freezing Staked KERNEL Tokens - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
`KernelDepositPool.initialize` never sets `maxNumberOfWithdrawalsPerUser`, leaving it at the Solidity default of `0`. Because `initiateWithdrawal` guards with `userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser`, the condition `0 >= 0` is always true, causing every withdrawal attempt to revert with `WithdrawalLimitReached`. Any user who stakes KERNEL tokens cannot retrieve them until an admin explicitly calls `setMaxNumberOfWithdrawalsPerUser`.

### Finding Description
`KernelDepositPool` is a Synthetix-style staking contract where users stake KERNEL tokens and earn rewards. Withdrawals are a two-step process: `initiateWithdrawal` reduces the user's staked balance and queues a withdrawal record, then `claimWithdrawal` releases the tokens after `withdrawalDelay` seconds.

The `initialize` function sets only `kernelToken`, `rewardsToken`, and the admin role. It does not initialize `maxNumberOfWithdrawalsPerUser` or `withdrawalDelay`:

```solidity
// contracts/KERNEL/KernelDepositPool.sol:259-271
function initialize(address _admin, address _kernelToken, address _rewardToken) external initializer {
    ...
    kernelToken = IERC20(_kernelToken);
    rewardsToken = IERC20(_rewardToken);
    // maxNumberOfWithdrawalsPerUser and withdrawalDelay are never set → both remain 0
}
```

The withdrawal guard in `initiateWithdrawal` is:

```solidity
// contracts/KERNEL/KernelDepositPool.sol:323
if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();
```

With `maxNumberOfWithdrawalsPerUser == 0`, the check evaluates to `0 >= 0 == true` for every caller, unconditionally reverting. No user can ever initiate a withdrawal in this state.

Additionally, `withdrawalDelay` also defaults to `0`. If admin sets `maxNumberOfWithdrawalsPerUser` but forgets `withdrawalDelay`, the intended time-lock protection is absent, removing the only mechanism that would prevent the stake/withdraw reward cycling described in the reference report.

### Impact Explanation
Any user who calls `stake` has their KERNEL tokens transferred into the contract. Because `initiateWithdrawal` always reverts when `maxNumberOfWithdrawalsPerUser == 0`, those tokens are inaccessible until an admin calls `setMaxNumberOfWithdrawalsPerUser`. If the admin key is lost or the admin is negligent, the freeze becomes permanent. This is a **temporary (potentially permanent) freezing of user funds** — Medium to Critical depending on admin availability.

### Likelihood Explanation
The condition is triggered by the very first withdrawal attempt by any staker. No special setup is required. Any user who stakes KERNEL tokens in a freshly deployed (or upgraded) `KernelDepositPool` before the admin configures `maxNumberOfWithdrawalsPerUser` is immediately affected. The likelihood is **High**.

### Recommendation
Set safe non-zero defaults for both `maxNumberOfWithdrawalsPerUser` and `withdrawalDelay` inside `initialize`:

```solidity
function initialize(address _admin, address _kernelToken, address _rewardToken) external initializer {
    ...
    kernelToken = IERC20(_kernelToken);
    rewardsToken = IERC20(_rewardToken);
    maxNumberOfWithdrawalsPerUser = 10;   // reasonable default
    withdrawalDelay = 7 days;             // reasonable default
}
```

This ensures the contract is functional immediately after deployment and that the withdrawal delay protection is active from the start.

### Proof of Concept

1. Deploy `KernelDepositPool` and call `initialize` (admin never calls `setMaxNumberOfWithdrawalsPerUser`).
2. User calls `stake(100e18)` — succeeds, tokens transferred in.
3. User calls `initiateWithdrawal(100e18)`:
   - `userWithdrawalIds[user].length` = 0
   - `maxNumberOfWithdrawalsPerUser` = 0
   - `0 >= 0` → `revert WithdrawalLimitReached()`
4. User's 100e18 KERNEL tokens are permanently locked in the contract.

Relevant code: [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L95-109)
```text
    /// @notice Delay (in seconds) before withdrawals can be claimed after initiation
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

**File:** contracts/KERNEL/KernelDepositPool.sol (L320-323)
```text
    function initiateWithdrawal(uint256 _amount) external nonReentrant updateReward(msg.sender) {
        if (_amount == 0) revert AmountZero();
        if (balanceOf[msg.sender] < _amount) revert InsufficientStakedBalance();
        if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();
```
