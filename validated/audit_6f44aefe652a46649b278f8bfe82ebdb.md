### Title
Uninitialized `updatedAt` Causes Permanently Inflated `rewardPerToken` on First Post-Reward Calculation - (File: contracts/KERNEL/KernelDepositPool.sol)

---

### Summary

In `KernelDepositPool`, the state variable `updatedAt` is never set in `initialize()`. When tokens are staked before `notifyRewardAmount` is called (the explicitly documented deployment pattern), `updatedAt` is set to `0` by the `updateReward` modifier because `lastTimeRewardApplicable()` returns `0` while `finishAt == 0`. After `notifyRewardAmount` sets `rewardRate` and `finishAt`, the first subsequent call to `rewardPerToken()` computes the elapsed time as `block.timestamp - 0`, producing an astronomically large reward-per-token value. This permanently freezes the affected user's unclaimed yield.

---

### Finding Description

`updatedAt` is declared at line 75 but is never assigned in `initialize()`: [1](#0-0) [2](#0-1) 

`lastTimeRewardApplicable()` returns `finishAt` when `finishAt < block.timestamp`. Since `finishAt` starts at `0`, it always returns `0` before `notifyRewardAmount` is called: [3](#0-2) 

The `updateReward` modifier therefore sets `updatedAt = 0` on every user action taken before `notifyRewardAmount`: [4](#0-3) 

The contract's own NatSpec comment explicitly documents that the intended deployment pattern is to have tokens staked **before** `notifyRewardAmount` is called: [5](#0-4) 

After `notifyRewardAmount` sets `rewardRate > 0` and `finishAt = block.timestamp + duration`, `updatedAt` remains `0`. The next call to `rewardPerToken()` computes:

```
rewardPerTokenStored + (rewardRate * (block.timestamp - 0) * 1e18) / totalKernelStaked
``` [6](#0-5) 

With `block.timestamp ≈ 1.7 × 10⁹` seconds, this produces a value orders of magnitude larger than the actual reward budget. The `updateReward` modifier then writes this inflated value into `rewards[user]`, which can never be transferred out because the contract holds only the actual reward allocation.

---

### Impact Explanation

**Permanent freezing of unclaimed yield (Medium).**

The first user to trigger `updateReward` after `notifyRewardAmount` has their `rewards[user]` set to an unclaimable amount (e.g., `rewardRate × 1.7e9 × 1e18 / totalKernelStaked`). Any call to `getReward()` by that user will revert because the contract cannot transfer a token amount far exceeding its balance: [7](#0-6) 

The inflated `rewardPerTokenStored` is also written to storage, but subsequent users who interact after this point receive `userRewardPerTokenPaid` equal to the inflated baseline, so their incremental rewards are computed correctly. The damage is isolated to the first affected user's accumulated `rewards[user]` entry, which is permanently frozen.

---

### Likelihood Explanation

**High.** The contract's own documentation explicitly states that the intended deployment sequence is to stake tokens *before* calling `notifyRewardAmount`. This means the vulnerable state (`updatedAt == 0` with `totalKernelStaked > 0` at the moment `notifyRewardAmount` is called) is the **normal deployment path**, not an edge case. Any staker who acts between deployment and the first `notifyRewardAmount` call — which is expected — will be affected on their next interaction.

---

### Recommendation

Initialize `updatedAt` to `block.timestamp` inside `initialize()`:

```solidity
function initialize(...) external initializer {
    ...
    updatedAt = block.timestamp;
    ...
}
```

This ensures `lastTimeRewardApplicable() - updatedAt` is never computed against epoch zero, regardless of when `notifyRewardAmount` is first called relative to user staking activity.

---

### Proof of Concept

1. Contract is deployed and `initialize()` is called. `updatedAt = 0`, `finishAt = 0`.
2. Alice calls `stake(1e18)`. `updateReward(Alice)` runs: `updatedAt = lastTimeRewardApplicable() = min(0, block.timestamp) = 0`. `totalKernelStaked = 1e18`.
3. Admin calls `notifyRewardAmount(1000e18)` (1000 reward tokens, 30-day duration). `rewardRate = 1000e18 / 2592000 ≈ 3.86e14`. `finishAt = block.timestamp + 2592000`. `updatedAt` is still `0`.
4. Alice calls `getReward()`. `updateReward(Alice)` runs:
   - `rewardPerToken() = 0 + (3.86e14 × block.timestamp × 1e18) / 1e18`
   - At `block.timestamp = 1_720_000_000`: `rewardPerToken ≈ 6.64e23`
   - `earned(Alice) = 1e18 × 6.64e23 / 1e18 = 6.64e23`
   - `rewards[Alice] = 6.64e23` (far exceeds the 1000e18 reward budget)
5. `rewardsToken.safeTransfer(Alice, 6.64e23)` reverts — Alice's yield is permanently frozen. [6](#0-5) [8](#0-7) [7](#0-6)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L18-23)
```text
 * @dev If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards
 *      for that period will stay locked in the contract. In this deployment, we're avoiding this issue by
 *      ensuring there are always some tokens staked before admin calls the `notifyRewardAmount` function,
 *      as well as for the entire duration of the reward period. Otherwise, staking just 1 wei by any address
 *      will ensure that the contract never has any unallocated rewards.
 */
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L74-76)
```text
    /// @notice The timestamp when the rewards were last updated
    uint256 public updatedAt;

```

**File:** contracts/KERNEL/KernelDepositPool.sol (L232-242)
```text
    modifier updateReward(address _account) {
        rewardPerTokenStored = rewardPerToken();
        updatedAt = lastTimeRewardApplicable();

        if (_account != address(0)) {
            rewards[_account] = earned(_account);
            userRewardPerTokenPaid[_account] = rewardPerTokenStored;
        }

        _;
    }
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

**File:** contracts/KERNEL/KernelDepositPool.sol (L400-402)
```text
    function lastTimeRewardApplicable() public view returns (uint256) {
        return finishAt < block.timestamp ? finishAt : block.timestamp;
    }
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L408-414)
```text
    function rewardPerToken() public view returns (uint256) {
        if (totalKernelStaked == 0) {
            return rewardPerTokenStored;
        }
        return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
            / totalKernelStaked;
    }
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L421-424)
```text
    function earned(address _account) public view returns (uint256) {
        return (balanceOf[_account] * (rewardPerToken() - userRewardPerTokenPaid[_account]) / DECIMAL_PRECISION)
            + rewards[_account];
    }
```
