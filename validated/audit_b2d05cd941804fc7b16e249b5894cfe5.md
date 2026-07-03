### Title
Missing Upper Bound on `_duration` in `setRewardsDuration` Enables DoS and Near-Zero Reward Rate — (File: `contracts/KERNEL/KernelDepositPool.sol`)

---

### Summary

`KernelDepositPool.setRewardsDuration()` validates only that `_duration != 0` but imposes no maximum cap. If an admin accidentally supplies an astronomically large value and subsequently calls `notifyRewardAmount`, `finishAt` is pushed far into the future, permanently DoS-ing `setRewardsDuration` and collapsing the effective reward rate to near-zero, freezing stakers' unclaimed yield.

---

### Finding Description

`setRewardsDuration` enforces a single lower-bound guard:

```solidity
if (_duration == 0) revert InvalidDuration();
duration = _duration;
``` [1](#0-0) 

No `MAX_DURATION` constant exists and no upper-bound check is present, in direct contrast to `setWithdrawalDelay`, which enforces both a zero check and `MAX_WITHDRAWAL_DELAY`:

```solidity
if (_withdrawalDelay > MAX_WITHDRAWAL_DELAY) revert MaximumWithdrawalDelayExceeded();
``` [2](#0-1) 

When `notifyRewardAmount` is subsequently called, two consequences follow:

1. **Near-zero reward rate**: `rewardRate = receivedAmount / duration` rounds to zero (or near-zero) for any realistic reward amount against a huge denominator.
2. **`finishAt` pushed far into the future**: `finishAt = block.timestamp + duration` becomes an enormous timestamp. [3](#0-2) 

The re-entry guard in `setRewardsDuration` then permanently blocks correction:

```solidity
if (finishAt >= block.timestamp) revert RewardDurationNotFinished();
``` [4](#0-3) 

There is no emergency override or pause path that bypasses this check, so the admin cannot reset `duration` until `finishAt` elapses — which may be centuries away.

---

### Impact Explanation

- **Permanent freezing of unclaimed yield (High)**: All stakers' accrued rewards become effectively zero because `rewardRate ≈ 0`. Rewards already deposited into the contract via `notifyRewardAmount` are locked for the entire (enormous) `duration` with no mechanism to recover them.
- **DoS on `setRewardsDuration` (Medium)**: The function is blocked for the lifetime of the erroneous period, preventing any corrective action.

---

### Likelihood Explanation

The admin role is a single privileged key. A fat-finger error (e.g., entering seconds instead of days, or an off-by-one in exponent) is a realistic operational mistake. The contract's own `setWithdrawalDelay` demonstrates awareness of this risk by capping its analogous parameter, making the omission here an inconsistency rather than a deliberate design choice.

---

### Recommendation

Introduce a `MAX_DURATION` constant (e.g., `365 days`) and add an upper-bound check in `setRewardsDuration`, mirroring the pattern already used in `setWithdrawalDelay`:

```solidity
uint256 public constant MAX_DURATION = 365 days;

function setRewardsDuration(uint256 _duration) external onlyRole(DEFAULT_ADMIN_ROLE) {
    if (finishAt >= block.timestamp) revert RewardDurationNotFinished();
    if (_duration == 0 || _duration > MAX_DURATION) revert InvalidDuration();
    duration = _duration;
    emit RewardsDurationUpdated(_duration);
}
```

---

### Proof of Concept

1. Admin calls `setRewardsDuration(type(uint256).max / 2)` — passes the only guard (`!= 0`). `duration` is now ~`5.7 × 10^76` seconds.
2. Admin calls `notifyRewardAmount(1_000_000e18)` (1 M reward tokens).
   - `rewardRate = 1_000_000e18 / (5.7e76) ≈ 0` — rounds to zero, triggering `revert RewardRateZero()`.
   - With a slightly smaller but still enormous value (e.g., `1e9 * 365 days`): `rewardRate` is non-zero but negligible; `finishAt = block.timestamp + 1e9 * 365 days` ≈ year 3.17 × 10^9.
3. Any subsequent call to `setRewardsDuration` reverts with `RewardDurationNotFinished` because `finishAt >> block.timestamp`.
4. All stakers calling `getReward()` receive near-zero rewards for the entire locked period; the deposited reward tokens are permanently stranded in the contract. [5](#0-4)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L552-592)
```text
    function setRewardsDuration(uint256 _duration) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (finishAt >= block.timestamp) revert RewardDurationNotFinished();
        if (_duration == 0) revert InvalidDuration();
        duration = _duration;
        emit RewardsDurationUpdated(_duration);
    }

    /**
     * @notice Notifies the contract about a new reward amount
     * @dev Uses a transfer-in pattern to determine the exact reward amount received.
     *      Also, to avoid undistributed rewards when no one is staked, this function reverts if totalKernelStaked is
     *      zero.
     * @param _amount The amount of reward tokens to add
     */
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
