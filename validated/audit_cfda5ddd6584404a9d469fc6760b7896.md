### Title
Reward Dust Permanently Locked Due to Integer Division Truncation in `notifyRewardAmount` - (File: contracts/KERNEL/KernelDepositPool.sol)

---

### Summary
In `KernelDepositPool.notifyRewardAmount`, integer division when computing `rewardRate` silently discards up to `duration - 1` reward tokens per reward period. These tokens are permanently locked in the contract and never distributed to stakers, mirroring the external report's class of incomplete reward delivery due to a silent balance shortfall.

---

### Finding Description
The `notifyRewardAmount` function computes the per-second reward rate using integer division:

```solidity
// Line 580
rewardRate = receivedAmount / duration;
```

and when extending an active period:

```solidity
// Lines 582-583
uint256 remaining = (finishAt - block.timestamp) * rewardRate;
rewardRate = (receivedAmount + remaining) / duration;
```

In both branches, the modulo remainder — `receivedAmount % duration` or `(receivedAmount + remaining) % duration` — is silently discarded. The contract will only ever distribute `rewardRate * duration` tokens over the period, which is strictly less than the tokens actually received. The difference is permanently locked in the contract with no recovery path for either stakers or admin.

The total tokens distributed to stakers via `getReward` over the full period is:

```
distributed = rewardRate * duration = (receivedAmount / duration) * duration
locked      = receivedAmount % duration   (up to duration - 1 tokens)
```

Each subsequent call to `notifyRewardAmount` compounds the locked dust because the `remaining` calculation uses the already-truncated `rewardRate`, not the actual token balance.

---

### Impact Explanation
**Low. Contract fails to deliver promised returns, but doesn't lose value.**

Stakers receive less than the full reward amount deposited by the admin. The shortfall is bounded by `duration - 1` tokens per reward period. With a typical `duration` of 7 days (604,800 seconds) and a reward token with 18 decimals, up to 604,799 wei-equivalent units are locked per period. Over many periods this accumulates. The tokens are not stolen — they remain in the contract — but stakers can never claim them and there is no admin sweep function.

---

### Likelihood Explanation
**High.** This truncation occurs on every single call to `notifyRewardAmount`, which is a routine, expected admin operation. No special conditions are required; the loss is structural and deterministic.

---

### Recommendation
Track the undistributed dust explicitly. One approach: after computing `rewardRate`, add the remainder back into the next period's reward pool by storing it in a `dustAccumulated` state variable and including it in the next `notifyRewardAmount` call's effective amount. Alternatively, add an admin-only `recoverDust` function that transfers the difference between the contract's `rewardsToken` balance and the theoretically owed amount to a treasury address.

---

### Proof of Concept

1. Admin sets `duration = 604800` (7 days in seconds).
2. Admin calls `notifyRewardAmount(604801)` — 604,801 reward tokens are transferred in.
3. Contract computes: `rewardRate = 604801 / 604800 = 1` token/second.
4. Over the full period, stakers collectively earn: `1 * 604800 = 604800` tokens.
5. The remaining `604801 - 604800 = 1` token is permanently locked.
6. Stakers call `getReward()` — the contract transfers `rewardAmount` per user, totalling 604,800 across all stakers. The 1 locked token is irrecoverable.
7. On the next `notifyRewardAmount(604801)` call (before `finishAt`): `remaining = (finishAt - block.timestamp) * 1`. The new `rewardRate = (604801 + remaining) / 604800`, again truncating a remainder. Dust compounds across periods.

**Relevant code:** [1](#0-0) [2](#0-1)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L382-389)
```text
    function getReward() external nonReentrant updateReward(msg.sender) {
        uint256 rewardAmount = rewards[msg.sender];

        if (rewardAmount > 0) {
            rewards[msg.sender] = 0;
            rewardsToken.safeTransfer(msg.sender, rewardAmount);
            emit RewardsClaimed(msg.sender, rewardAmount);
        }
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L579-584)
```text
        if (block.timestamp >= finishAt) {
            rewardRate = receivedAmount / duration;
        } else {
            uint256 remaining = (finishAt - block.timestamp) * rewardRate;
            rewardRate = (receivedAmount + remaining) / duration;
        }
```
