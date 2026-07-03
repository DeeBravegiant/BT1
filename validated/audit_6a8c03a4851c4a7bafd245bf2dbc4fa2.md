Looking at the exact check in `initiateWithdrawal` and the initialization path:

The vulnerability path is real and concrete. Here is the full analysis:

---

### Title
Uninitialized `maxNumberOfWithdrawalsPerUser` Permanently Blocks `initiateWithdrawal` for All Stakers — (`contracts/KERNEL/KernelDepositPool.sol`)

### Summary
`KernelDepositPool.initialize()` never sets `maxNumberOfWithdrawalsPerUser`, leaving it at the Solidity default of `0`. Because `initiateWithdrawal` guards with `userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser`, and `0 >= 0` is always `true`, every call to `initiateWithdrawal` reverts with `WithdrawalLimitReached` until an admin separately calls `setMaxNumberOfWithdrawalsPerUser`. Tokens staked via `claimAndStake` (or plain `stake`) before that admin call are frozen.

### Finding Description

`initialize()` sets only `kernelToken`, `rewardsToken`, and the admin role: [1](#0-0) 

`maxNumberOfWithdrawalsPerUser` is never assigned, so it remains `0`.

`initiateWithdrawal` then evaluates:

```solidity
if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();
``` [2](#0-1) 

For any user with zero open withdrawals: `0 >= 0` → `true` → revert. The gate is permanently closed until the admin calls `setMaxNumberOfWithdrawalsPerUser`.

`stakeFor`, called by `claimAndStake`, has no corresponding guard: [3](#0-2) 

So staking succeeds while withdrawal is blocked. The `claimAndStake` path in `KernelMerkleDistributor` calls `stakeFor` directly: [4](#0-3) 

### Impact Explanation

Any KERNEL tokens staked (via `stake`, `stakeFor`, or `claimAndStake`) before the admin calls `setMaxNumberOfWithdrawalsPerUser` are frozen — `initiateWithdrawal` reverts for every user. The freeze lifts only after the admin executes the setter. Because the admin retains the ability to call `setMaxNumberOfWithdrawalsPerUser` at any time, the freeze is **temporary**, not permanent.

Correct scoped impact: **Medium — Temporary freezing of funds.**

The question's claim of *Critical / permanent* freezing is overstated: `setMaxNumberOfWithdrawalsPerUser` is callable by the admin at any time and immediately unblocks withdrawals. [5](#0-4) 

### Likelihood Explanation

Moderate. The deployment sequence requires the admin to remember a separate post-`initialize` call. There is no on-chain enforcement (no check in `initialize`, no check in `stake`/`stakeFor`) that prevents staking before the limit is set. Any user who stakes during this window is affected.

### Recommendation

Set a safe default for `maxNumberOfWithdrawalsPerUser` inside `initialize()`, e.g.:

```solidity
maxNumberOfWithdrawalsPerUser = MAX_WITHDRAWALS_PER_USER; // 100
```

Alternatively, add a guard in `stake` and `stakeFor` that reverts if `maxNumberOfWithdrawalsPerUser == 0`, preventing staking before the parameter is configured.

### Proof of Concept

```solidity
// 1. Deploy KernelDepositPool; do NOT call setMaxNumberOfWithdrawalsPerUser
// 2. Deploy KernelMerkleDistributor pointing at the pool; set merkle root
// 3. User calls claimAndStake() → stakeFor() succeeds, balanceOf[user] > 0
// 4. User calls initiateWithdrawal(amount)
//    → userWithdrawalIds[user].length == 0
//    → maxNumberOfWithdrawalsPerUser == 0
//    → 0 >= 0 == true → revert WithdrawalLimitReached
// 5. Assert: user's KERNEL is locked in the pool with no exit path
```

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L323-323)
```text
        if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();
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

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L270-285)
```text
    function claimAndStake(
        uint256 index,
        address account,
        uint256 cumulativeAmount,
        bytes32[] calldata merkleProof
    )
        external
        nonReentrant
        whenNotPaused
    {
        uint256 amountToStake = _processClaim(index, account, cumulativeAmount, merkleProof);

        IKernelDepositPool(kernelDepositPool).stakeFor(account, amountToStake);

        emit ClaimedAndStaked(index, account, amountToStake);
    }
```
