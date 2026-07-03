### Title
Reward Tokens Permanently Frozen When `totalKernelStaked` Drops to Zero During Active Reward Period — (`contracts/KERNEL/KernelDepositPool.sol`)

### Summary

`KernelDepositPool` contains a Synthetix-style staking rewards mechanism. When all stakers call `initiateWithdrawal()` during an active reward period, `totalKernelStaked` immediately drops to zero, causing `rewardPerToken()` to stop accumulating. The remaining reward tokens for the rest of the period are permanently locked in the contract with no recovery path. The `notifyRewardAmount()` guard (`if (totalKernelStaked == 0) revert NoStakedTokens()`) then also blocks the admin from starting a new reward period, compounding the freeze.

### Finding Description

`initiateWithdrawal()` immediately decrements `totalKernelStaked` before the withdrawal delay elapses: [1](#0-0) 

When `totalKernelStaked` reaches zero, `rewardPerToken()` short-circuits and returns the stored value unchanged, so no further rewards accrue to anyone: [2](#0-1) 

The reward tokens already deposited for the current period (calculated as `rewardRate × remainingTime`) sit in the contract balance but are never attributed to any staker. There is no `rescueTokens`, sweep, or rollover function in the contract.

After the period ends, the admin cannot start a new period because `notifyRewardAmount()` guards against `totalKernelStaked == 0`: [3](#0-2) 

`notifyRewardAmount()` uses a `balanceAfter - balanceBefore` pattern to count only newly transferred tokens as `receivedAmount`: [4](#0-3) 

So even after someone re-stakes and the admin can call `notifyRewardAmount` again, the old stranded tokens are never rolled into the new `rewardRate`. They are permanently unrecoverable.

The contract's own NatSpec acknowledges this risk but relies entirely on an off-chain operational guarantee: [5](#0-4) 

This off-chain guarantee is not enforced on-chain and can be violated by any staker acting within their normal rights.

### Impact Explanation

**Medium — Permanent freezing of unclaimed yield.**

Reward tokens deposited by the admin for a distribution period are permanently locked in `KernelDepositPool` whenever `totalKernelStaked` reaches zero mid-period. There is no admin rescue function, no rollover mechanism, and no way to include the stranded balance in a future `notifyRewardAmount` call. The yield owed to stakers for the remainder of that period is destroyed.

### Likelihood Explanation

**Medium.** Any scenario where all stakers legitimately exit during an active reward period triggers the freeze — this requires no malicious intent. A single large staker who holds the majority of `totalKernelStaked` can unilaterally cause this by calling `initiateWithdrawal()` for their full balance. The withdrawal delay does not prevent the damage because `totalKernelStaked` is decremented at initiation time, not at claim time. [6](#0-5) 

### Recommendation

1. **Track unallocated rewards**: Record the reward tokens that were not distributed when `totalKernelStaked` was zero and roll them into the next `notifyRewardAmount` call by reading the contract's existing balance (not just the newly transferred amount).
2. **Remove or relax the `NoStakedTokens` guard** in `notifyRewardAmount` so the admin can at least restart a period once stakers return, and include the stranded balance in the new `rewardRate`.
3. **Add an admin rescue function** to recover stranded reward tokens to a treasury if the protocol decides to wind down a reward period.

### Proof of Concept

```
1. Admin calls notifyRewardAmount(1000e18):
   - 1000 reward tokens transferred in
   - rewardRate = 1000e18 / duration
   - finishAt = block.timestamp + duration

2. Alice stakes 1000e18 KERNEL tokens → totalKernelStaked = 1000e18

3. Half the reward period elapses → ~500e18 reward tokens accrued to Alice

4. Alice calls initiateWithdrawal(1000e18):
   - totalKernelStaked = 0  (immediately, before withdrawal delay)
   - rewardPerToken() now returns rewardPerTokenStored unchanged
   - Remaining ~500e18 reward tokens stop accruing to anyone

5. Alice waits withdrawalDelay, calls claimWithdrawal → gets KERNEL back

6. After finishAt, admin calls notifyRewardAmount(newAmount):
   - Reverts: NoStakedTokens (totalKernelStaked == 0)

7. Alice re-stakes 1 wei → totalKernelStaked = 1

8. Admin calls notifyRewardAmount(newAmount):
   - balanceBefore = 500e18 (stranded) + 0 (no new tokens yet)
   - After transfer: balanceAfter = 500e18 + newAmount
   - receivedAmount = newAmount  ← stranded 500e18 is NOT included
   - rewardRate = newAmount / duration  ← 500e18 permanently lost
```

The ~500e18 stranded reward tokens remain in the contract forever with no mechanism to recover or redistribute them.

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L17-23)
```text
 * @dev Modified from https://github.com/Synthetixio/synthetix/blob/develop/contracts/StakingRewards.sol
 * @dev If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards
 *      for that period will stay locked in the contract. In this deployment, we're avoiding this issue by
 *      ensuring there are always some tokens staked before admin calls the `notifyRewardAmount` function,
 *      as well as for the entire duration of the reward period. Otherwise, staking just 1 wei by any address
 *      will ensure that the contract never has any unallocated rewards.
 */
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

**File:** contracts/KERNEL/KernelDepositPool.sol (L408-411)
```text
    function rewardPerToken() public view returns (uint256) {
        if (totalKernelStaked == 0) {
            return rewardPerTokenStored;
        }
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L566-570)
```text
    function notifyRewardAmount(uint256 _amount) external onlyRole(DEFAULT_ADMIN_ROLE) updateReward(address(0)) {
        if (_amount == 0) revert AmountZero();

        // Prevent starting a reward period when no tokens are staked to avoid unallocated rewards
        if (totalKernelStaked == 0) revert NoStakedTokens();
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L573-580)
```text
        uint256 balanceBefore = rewardsToken.balanceOf(address(this));
        rewardsToken.safeTransferFrom(msg.sender, address(this), _amount);
        uint256 balanceAfter = rewardsToken.balanceOf(address(this));
        // Calculate the actual amount of tokens received in case of a transfer fee (tax)
        uint256 receivedAmount = balanceAfter - balanceBefore;

        if (block.timestamp >= finishAt) {
            rewardRate = receivedAmount / duration;
```
