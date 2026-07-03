### Title
User Can Stake Before `maxNumberOfWithdrawalsPerUser` Is Set, Temporarily Freezing Staked Principal - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
`KernelDepositPool.stake()` imposes no prerequisite that `maxNumberOfWithdrawalsPerUser` be configured before accepting deposits. Because `maxNumberOfWithdrawalsPerUser` is never set in `initialize()` and defaults to `0`, any user who stakes before the admin calls `setMaxNumberOfWithdrawalsPerUser()` will find their staked KERNEL principal permanently unwithdrawable until the admin acts, because `initiateWithdrawal()` unconditionally reverts when the limit is zero.

### Finding Description
`KernelDepositPool.initialize()` sets only `kernelToken`, `rewardsToken`, and the admin role. It does not initialize `maxNumberOfWithdrawalsPerUser`, leaving it at the Solidity default of `0`. [1](#0-0) 

`stake()` has no guard requiring `maxNumberOfWithdrawalsPerUser > 0` before accepting tokens: [2](#0-1) 

`initiateWithdrawal()` contains the check:

```solidity
if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();
``` [3](#0-2) 

When `maxNumberOfWithdrawalsPerUser == 0`, a fresh staker has `userWithdrawalIds[msg.sender].length == 0`. The condition `0 >= 0` evaluates to `true`, so every call to `initiateWithdrawal()` reverts with `WithdrawalLimitReached`. Because `claimWithdrawal()` requires a prior successful `initiateWithdrawal()`, the staked principal is completely inaccessible.

`setMaxNumberOfWithdrawalsPerUser()` explicitly rejects `0` as a value, so the admin cannot "fix" the state by setting it to zero; they must set a positive value: [4](#0-3) 

### Impact Explanation
Any user who calls `stake()` before the admin calls `setMaxNumberOfWithdrawalsPerUser()` has their KERNEL tokens locked in the contract with no on-chain path to recover them until the admin acts. This is a **temporary freezing of funds** (Medium impact per the allowed scope). The freeze is not permanent because the admin can unblock it, but the user has no recourse on their own and cannot predict when or whether the admin will act.

### Likelihood Explanation
`KernelDepositPool` is an upgradeable contract whose `initialize()` function is callable by anyone who deploys or upgrades it. The `stake()` function is public and unrestricted. There is no deployment script enforcement, no on-chain guard, and no event or revert that warns a user the contract is not yet fully configured. A user who reads the ABI and calls `stake()` immediately after deployment — before the admin completes setup — will be affected. This is a realistic race condition during the deployment window.

### Recommendation
Initialize `maxNumberOfWithdrawalsPerUser` to a safe non-zero default inside `initialize()`, or add a guard to `stake()` that reverts when `maxNumberOfWithdrawalsPerUser == 0`:

```solidity
function stake(uint256 _amount) external nonReentrant updateReward(msg.sender) {
    if (_amount == 0) revert AmountZero();
+   if (maxNumberOfWithdrawalsPerUser == 0) revert WithdrawalLimitNotConfigured();
    ...
}
```

Alternatively, set a default in `initialize()`:

```solidity
maxNumberOfWithdrawalsPerUser = MAX_WITHDRAWALS_PER_USER; // 100
```

### Proof of Concept
1. Admin deploys and calls `KernelDepositPool.initialize(admin, kernelToken, rewardToken)`. `maxNumberOfWithdrawalsPerUser` is `0`.
2. Alice calls `stake(1000e18)`. Succeeds — her 1000 KERNEL are transferred in, `balanceOf[Alice] = 1000e18`.
3. Alice calls `initiateWithdrawal(1000e18)`.
   - Check: `userWithdrawalIds[Alice].length >= maxNumberOfWithdrawalsPerUser` → `0 >= 0` → `true` → reverts `WithdrawalLimitReached`.
4. Alice has no other on-chain path to recover her principal. `claimWithdrawal` requires a withdrawal ID that can only be created by `initiateWithdrawal`.
5. Alice's 1000 KERNEL remain frozen until the admin calls `setMaxNumberOfWithdrawalsPerUser(N)` for some `N > 0`. [5](#0-4)

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
