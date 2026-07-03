### Title
Reward Token Dust Permanently Locked via Integer Truncation in `notifyRewardAmount` — (`contracts/KERNEL/KernelDepositPool.sol`)

---

### Summary

Integer division in `notifyRewardAmount` silently discards up to `duration - 1` reward tokens per call. Because no sweep/recovery function exists for the rewards token, these residues accumulate as irrecoverable dust in the contract across every reward period.

---

### Finding Description

In `notifyRewardAmount`, `rewardRate` is computed via integer division:

```solidity
// Line 580 — new period
rewardRate = receivedAmount / duration;

// Line 583 — mid-period top-up
rewardRate = (receivedAmount + remaining) / duration;
``` [1](#0-0) 

The truncation residue is `receivedAmount % duration` (up to `duration - 1` tokens). Over a full period, stakers collectively earn only `rewardRate * duration`, which equals `receivedAmount - (receivedAmount % duration)`. The residue stays in the contract.

**Mid-period compounding:** When `notifyRewardAmount` is called before `finishAt`, the `remaining` value is:

```solidity
uint256 remaining = (finishAt - block.timestamp) * rewardRate;
``` [2](#0-1) 

This reconstructs only the tokens that *would* be emitted at the current rate — it does **not** include the prior period's truncation residue already sitting in the contract. So the residue from period 1 is silently excluded from the new `rewardRate` computation, and a second truncation is applied on top. Each successive `notifyRewardAmount` call compounds the locked dust.

**No recovery path:** The contract has no `recoverERC20`, sweep, or admin withdrawal function for `rewardsToken`. The only exit for reward tokens is `getReward()`, which pays only `earned()` amounts derived from `rewardRate`. The dust is permanently unclaimable. [3](#0-2) 

---

### Impact Explanation

**Medium — Permanent freezing of unclaimed yield.**

The tokens are not stolen by any party; they are locked in the contract with no mechanism to retrieve them. The maximum loss per `notifyRewardAmount` call is `duration - 1` token units. For an 18-decimal token with a 7-day duration (`604800` seconds), the maximum per-call loss is `604799 wei` (~6×10⁻¹³ tokens) — negligible in isolation. However, for tokens with fewer decimals or very large durations, the absolute loss per period grows. Across many periods and mid-period top-ups, the dust accumulates and is never distributable.

The claimed scope of **"High — Theft of unclaimed yield"** does not apply because no party benefits from the locked tokens. The correct scope is **"Medium — Permanent freezing of unclaimed yield."**

---

### Likelihood Explanation

Certain. This occurs on every single `notifyRewardAmount` call whenever `receivedAmount % duration != 0`. No attacker action is required; it is a systemic property of the integer arithmetic. The only scenario where it does not occur is if the admin always sends amounts that are exact multiples of `duration`, which is not enforced anywhere.

---

### Recommendation

1. **Track and roll over the residue:** After computing `rewardRate`, calculate `leftover = receivedAmount - rewardRate * duration` and add it to the next period's `receivedAmount`.
2. **Alternatively**, add an admin-callable `recoverERC20` function that can only sweep tokens in excess of `rewardRate * (finishAt - block.timestamp)` (i.e., the provably unearnable dust).
3. **Document the known limitation** if the amounts are deemed acceptable (as the Synthetix original does), so operators can account for it off-chain.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Foundry fuzz test (pseudo-code, run against a local fork)
function testTruncationDust(uint128 amount, uint32 dur) public {
    vm.assume(dur > 1 && amount > 0);
    pool.setRewardsDuration(dur);

    // Period 1
    rewardsToken.approve(address(pool), amount);
    pool.notifyRewardAmount(amount);

    // Fast-forward to end of period
    vm.warp(block.timestamp + dur);

    // All stakers claim
    pool.getReward();

    // Residue locked in contract
    uint256 locked = rewardsToken.balanceOf(address(pool));
    // locked == amount % dur  (up to dur-1 tokens permanently frozen)
    assertEq(locked, uint256(amount) % uint256(dur));
    assertTrue(locked > 0 || uint256(amount) % uint256(dur) == 0);
}
```

The assertion `locked == amount % dur` will hold for any non-divisible `(amount, dur)` pair, confirming that `amount % duration` tokens are permanently frozen per period with no recovery path.

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L579-584)
```text
        if (block.timestamp >= finishAt) {
            rewardRate = receivedAmount / duration;
        } else {
            uint256 remaining = (finishAt - block.timestamp) * rewardRate;
            rewardRate = (receivedAmount + remaining) / duration;
        }
```
