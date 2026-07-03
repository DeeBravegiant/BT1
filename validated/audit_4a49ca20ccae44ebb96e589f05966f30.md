### Title
Uninitialized `withdrawalDelay` Enables Flash-Staking to Steal Yield - (File: `contracts/KERNEL/KernelDepositPool.sol`)

---

### Summary

`KernelDepositPool` is a Synthetix-style staking contract where users stake KERNEL tokens to earn reward tokens. The contract includes a `withdrawalDelay` mechanism intended to prevent flash-staking attacks. However, `withdrawalDelay` is never initialized in `initialize()` and defaults to `0`. If `notifyRewardAmount()` is called before `setWithdrawalDelay()`, any unprivileged user can stake a large amount, claim a disproportionate share of rewards, and immediately withdraw in the same block — stealing yield from legitimate long-term stakers.

---

### Finding Description

`KernelDepositPool` declares `withdrawalDelay` as a plain `uint256` state variable with no default assignment: [1](#0-0) 

The `initialize()` function never sets this variable: [2](#0-1) 

The admin-only `notifyRewardAmount()` function, which starts a reward distribution period, only guards against `totalKernelStaked == 0` — it does **not** require `withdrawalDelay > 0`: [3](#0-2) 

When a user calls `initiateWithdrawal()`, the unlock time is computed as:

```solidity
uint256 unlockTime = block.timestamp + withdrawalDelay;
```

With `withdrawalDelay == 0`, this becomes `unlockTime = block.timestamp`. [4](#0-3) 

The `claimWithdrawal()` guard is:

```solidity
if (block.timestamp < withdrawal.unlockTime) revert WithdrawalNotReady();
```

Since `block.timestamp >= block.timestamp` is always true, the withdrawal is claimable in the **same block** it was initiated. [5](#0-4) 

The `setWithdrawalDelay()` admin function explicitly rejects `0`, meaning once set it cannot be reset to `0` — but the initial state is `0` and there is no enforcement that it must be set before rewards begin: [6](#0-5) 

---

### Impact Explanation

**High — Theft of unclaimed yield.**

Attack steps (all executable in a single transaction or across a few blocks):

1. Observe that `withdrawalDelay == 0` and a reward period is active (`rewardRate > 0`, `finishAt > block.timestamp`).
2. Call `stake(largeAmount)` — the `updateReward` modifier snapshots the current `rewardPerTokenStored` for the attacker.
3. Wait one or more blocks for `rewardPerToken()` to increase (the Synthetix model accrues rewards per second).
4. Call `getReward()` — attacker claims rewards proportional to `largeAmount / totalKernelStaked` for the elapsed time.
5. Call `initiateWithdrawal(largeAmount)` — `unlockTime = block.timestamp`.
6. Call `claimWithdrawal(id)` in the same block — receives `largeAmount` of KERNEL back immediately.

Because the attacker holds a dominant share of `totalKernelStaked` during the window, legitimate stakers receive a drastically reduced share of rewards for that period. The attacker recovers their principal immediately with no lock-up cost.

---

### Likelihood Explanation

**Medium.** The deployment sequence where `notifyRewardAmount()` is called before `setWithdrawalDelay()` is realistic — the contract comment itself notes that the admin must ensure tokens are staked before calling `notifyRewardAmount`, but places no analogous requirement on `withdrawalDelay`. Any on-chain observer can detect `withdrawalDelay == 0` and an active reward period and execute the attack permissionlessly.

---

### Recommendation

1. **Initialize `withdrawalDelay` to a safe non-zero value** (e.g., `7 days`) inside `initialize()`.
2. **Add a guard in `notifyRewardAmount()`** that reverts if `withdrawalDelay == 0`, preventing reward periods from starting before the delay is configured:
   ```solidity
   if (withdrawalDelay == 0) revert InvalidWithdrawalDelay();
   ```

---

### Proof of Concept

```solidity
// Precondition: withdrawalDelay == 0 (never initialized), reward period active

// 1. Attacker stakes large amount
kernelDepositPool.stake(1_000_000e18);

// 2. Advance 1 block (rewards accrue for 1 block duration)
vm.roll(block.number + 1);
vm.warp(block.timestamp + 12);

// 3. Claim rewards — attacker captures majority share
kernelDepositPool.getReward();

// 4. Initiate withdrawal — unlockTime = block.timestamp (delay == 0)
kernelDepositPool.initiateWithdrawal(1_000_000e18);

// 5. Claim withdrawal immediately in same block
kernelDepositPool.claimWithdrawal(1);

// Result: attacker recovers full principal + disproportionate rewards
// Legitimate stakers earned near-zero rewards for the same period
``` [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L96-96)
```text
    uint256 public withdrawalDelay;
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

**File:** contracts/KERNEL/KernelDepositPool.sol (L355-357)
```text
        if (block.timestamp < withdrawal.unlockTime) {
            revert WithdrawalNotReady();
        }
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L382-390)
```text
    function getReward() external nonReentrant updateReward(msg.sender) {
        uint256 rewardAmount = rewards[msg.sender];

        if (rewardAmount > 0) {
            rewards[msg.sender] = 0;
            rewardsToken.safeTransfer(msg.sender, rewardAmount);
            emit RewardsClaimed(msg.sender, rewardAmount);
        }
    }
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L566-592)
```text
    function notifyRewardAmount(uint256 _amount) external onlyRole(DEFAULT_ADMIN_ROLE) updateReward(address(0)) {
        if (_amount == 0) revert AmountZero();

        // Prevent starting a reward period when no tokens are staked to avoid unallocated rewards
        if (totalKernelStaked == 0) revert NoStakedTokens();

        // Transfer reward tokens into the contract
        uint256 balanceBefore = rewardsToken.balanceOf(address(this));
        rewardsToken.safeTransferFrom(msg.sender, address(this), _amount);
        uint256 balanceAfter = rewardsToken.balanceOf(address(this));
        // Calculate the actual amount of tokens received in case of a transfer fee (tax)
        uint256 receivedAmount = balanceAfter - balanceBefore;

        if (block.timestamp >= finishAt) {
            rewardRate = receivedAmount / duration;
        } else {
            uint256 remaining = (finishAt - block.timestamp) * rewardRate;
            rewardRate = (receivedAmount + remaining) / duration;
        }

        if (rewardRate == 0) revert RewardRateZero();

        finishAt = block.timestamp + duration;
        updatedAt = block.timestamp;

        emit NotifyRewardAmount(receivedAmount, finishAt);
    }
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L598-604)
```text
    function setWithdrawalDelay(uint256 _withdrawalDelay) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_withdrawalDelay == 0) revert InvalidWithdrawalDelay();
        if (_withdrawalDelay > MAX_WITHDRAWAL_DELAY) revert MaximumWithdrawalDelayExceeded();

        withdrawalDelay = _withdrawalDelay;
        emit WithdrawalDelayUpdated(_withdrawalDelay);
    }
```
