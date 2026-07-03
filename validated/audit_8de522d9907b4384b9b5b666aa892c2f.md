### Title
Uninitialized `maxNumberOfWithdrawalsPerUser` Permanently Blocks All Users From Initiating Withdrawals While Staking Remains Open - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
`KernelDepositPool.initialize()` never sets `maxNumberOfWithdrawalsPerUser`, leaving it at the Solidity default of `0`. The guard in `initiateWithdrawal` evaluates `0 >= 0 == true` and always reverts with `WithdrawalLimitReached`, freezing every user's staked KERNEL tokens. Meanwhile `stake()` and `stakeFor()` impose no such check, so tokens continue to accumulate in the contract with no exit path.

### Finding Description

`KernelDepositPool.initialize()` initialises only `kernelToken`, `rewardsToken`, and the admin role. It never assigns `maxNumberOfWithdrawalsPerUser`, so the variable retains its default value of `0`. [1](#0-0) 

`initiateWithdrawal` guards the withdrawal path with:

```solidity
if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser)
    revert WithdrawalLimitReached();
``` [2](#0-1) 

Because `maxNumberOfWithdrawalsPerUser == 0`, the condition `0 >= 0` is always `true` for every caller, so `WithdrawalLimitReached` is thrown unconditionally. No user can ever call `initiateWithdrawal` until an admin explicitly calls `setMaxNumberOfWithdrawalsPerUser`.

`setMaxNumberOfWithdrawalsPerUser` itself rejects a zero argument, so there is no self-healing path from within the contract:

```solidity
if (_maxNumberOfWithdrawalsPerUser == 0 || _maxNumberOfWithdrawalsPerUser > MAX_WITHDRAWALS_PER_USER)
    revert InvalidMaxNumberOfWithdrawalsPerUser();
``` [3](#0-2) 

In contrast, `stake()` and `stakeFor()` carry no such guard and succeed freely: [4](#0-3) [5](#0-4) 

`KernelTop100MerkleDistributor` calls `kernelDepositPool.stakeFor(account, amount)` when users claim-and-stake, depositing tokens into the pool on behalf of users who then cannot withdraw them: [6](#0-5) 

### Impact Explanation

All KERNEL tokens staked in `KernelDepositPool` — whether deposited directly via `stake()` or on a user's behalf via `stakeFor()` from `KernelTop100MerkleDistributor` — are frozen until an admin calls `setMaxNumberOfWithdrawalsPerUser`. If the admin call is delayed or never made, the freeze is permanent. This matches the **Critical: Permanent freezing of funds** impact class. At minimum it is **Medium: Temporary freezing of funds**.

### Likelihood Explanation

The contract is deployed and immediately usable for staking. Any user who stakes (or receives a stake via the distributor) before the admin sets `maxNumberOfWithdrawalsPerUser` has their funds frozen. Because `initialize()` is the standard deployment entry point and the variable is not set there, this condition exists from block 0 of the contract's life. The likelihood is high for any deployment that does not atomically call `setMaxNumberOfWithdrawalsPerUser` in the same transaction as `initialize`.

### Recommendation

Set a safe default for `maxNumberOfWithdrawalsPerUser` inside `initialize()`, for example:

```solidity
function initialize(address _admin, address _kernelToken, address _rewardToken) external initializer {
    // ... existing checks ...
    maxNumberOfWithdrawalsPerUser = MAX_WITHDRAWALS_PER_USER; // or a chosen safe default
}
```

This ensures the withdrawal path is open from deployment and is consistent with the existing constant `MAX_WITHDRAWALS_PER_USER = 100`. [7](#0-6) 

### Proof of Concept

1. Deploy `KernelDepositPool` via `initialize(admin, kernelToken, rewardToken)`. `maxNumberOfWithdrawalsPerUser` is `0`.
2. Admin grants `STAKE_FOR_ROLE` to `KernelTop100MerkleDistributor`.
3. User calls `claimAndStake(amount, proof)` on the distributor → `stakeFor(user, amount)` is called → `balanceOf[user] += amount`. Succeeds.
4. User calls `initiateWithdrawal(amount)` on `KernelDepositPool`.
   - Check: `userWithdrawalIds[user].length (= 0) >= maxNumberOfWithdrawalsPerUser (= 0)` → `true`
   - Reverts with `WithdrawalLimitReached`.
5. User's KERNEL tokens are permanently locked in the contract with no withdrawal path available. [8](#0-7)

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L296-314)
```text
    function stakeFor(
        address _account,
        uint256 _amount
    )
        external
        nonReentrant
        onlyRole(STAKE_FOR_ROLE)
        updateReward(_account)
    {
        UtilLib.checkNonZeroAddress(_account);

        if (_amount == 0) revert AmountZero();

        balanceOf[_account] += _amount;
        totalKernelStaked += _amount;
        kernelToken.safeTransferFrom(msg.sender, address(this), _amount);

        emit StakedFor(msg.sender, _account, _amount);
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

**File:** contracts/KERNEL/KernelDepositPool.sol (L614-616)
```text
        if (_maxNumberOfWithdrawalsPerUser == 0 || _maxNumberOfWithdrawalsPerUser > MAX_WITHDRAWALS_PER_USER) {
            revert InvalidMaxNumberOfWithdrawalsPerUser();
        }
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L22-29)
```text
interface IKernelDepositPool {
    /**
     * @notice Allows a user to stake tokens on behalf of another user
     * @param _account The address of the account to stake for
     * @param _amount The amount of staking tokens to stake
     */
    function stakeFor(address _account, uint256 _amount) external;
}
```
