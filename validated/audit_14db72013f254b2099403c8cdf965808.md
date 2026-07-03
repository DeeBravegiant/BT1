### Title
`getReward` Fails Silently When No Rewards Are Available - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
The `getReward()` function in `KernelDepositPool` uses an `if (rewardAmount > 0)` guard to conditionally transfer rewards. When the condition is not satisfied, the function returns successfully without transferring anything, without reverting, and without emitting any event — giving the caller no indication that no rewards were paid out.

### Finding Description
In `KernelDepositPool.getReward()` (lines 382–390), the entire reward-transfer logic is wrapped in a single `if` block:

```solidity
function getReward() external nonReentrant updateReward(msg.sender) {
    uint256 rewardAmount = rewards[msg.sender];

    if (rewardAmount > 0) {
        rewards[msg.sender] = 0;
        rewardsToken.safeTransfer(msg.sender, rewardAmount);
        emit RewardsClaimed(msg.sender, rewardAmount);
    }
}
```

If `rewardAmount == 0`, the function exits silently: no revert, no event, no return value. The transaction succeeds on-chain with no observable effect. This is structurally identical to the reported `payUser` pattern — an `if` clause guards the core action, and the `else` branch (the zero-reward case) is entirely unhandled. [1](#0-0) 

### Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**

A KERNEL staker calls `getReward()` expecting to receive accrued `rewardsToken`. If no rewards have accrued (e.g., the reward period has not started, the user staked too recently, or `totalKernelStaked` was zero during the period), the call succeeds silently. The caller — especially a smart contract integrator — receives no signal distinguishing "rewards were paid" from "no rewards existed." Off-chain tooling or integrating contracts that rely on a successful transaction as confirmation of a reward transfer will be silently misled.

### Likelihood Explanation
Any staker who calls `getReward()` before rewards have accrued, immediately after staking, or after the reward period has ended will trigger this path. This is a normal operational scenario, not an edge case. The likelihood is **medium** — it occurs in routine usage without any adversarial action required.

### Recommendation
Replace the silent `if` with an explicit revert when there are no rewards to claim:

```solidity
function getReward() external nonReentrant updateReward(msg.sender) {
    uint256 rewardAmount = rewards[msg.sender];
    if (rewardAmount == 0) revert NoRewardsToClaim();

    rewards[msg.sender] = 0;
    rewardsToken.safeTransfer(msg.sender, rewardAmount);
    emit RewardsClaimed(msg.sender, rewardAmount);
}
```

This matches the pattern already used elsewhere in the codebase (e.g., `KernelMerkleDistributor._processClaim` reverts with `NoTokensToClaim()` when `claimableAmount == 0`). [2](#0-1) 

### Proof of Concept

1. Alice stakes KERNEL tokens via `stake()` in `KernelDepositPool`.
2. Alice immediately calls `getReward()` before any reward epoch has elapsed (or before `notifyRewardAmount` has been called).
3. `updateReward(msg.sender)` runs — `rewards[msg.sender]` remains `0`.
4. The `if (rewardAmount > 0)` condition is false; the function returns with no transfer and no event.
5. Alice's transaction is confirmed on-chain as successful. No `RewardsClaimed` event is emitted. Alice (or her integrating contract) has no on-chain signal that the call was a no-op. [3](#0-2)

### Citations

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

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L329-331)
```text
        if (claimableAmount == 0) {
            revert NoTokensToClaim();
        }
```
