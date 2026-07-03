### Title
Reward Tokens Permanently Locked When `totalKernelStaked` Drops to Zero During Active Reward Period - (File: `contracts/KERNEL/KernelDepositPool.sol`)

---

### Summary

`KernelDepositPool.sol` implements a Synthetix-style staking rewards model. When `totalKernelStaked` reaches zero during an active reward distribution window, the `rewardPerToken()` function stops accumulating rewards. Any reward tokens already transferred into the contract for that period are permanently locked with no on-chain recovery mechanism.

---

### Finding Description

The `rewardPerToken()` function freezes reward accumulation whenever `totalKernelStaked == 0`:

```solidity
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored; // rewards stop accumulating
    }
    return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
``` [1](#0-0) 

When `totalKernelStaked` is zero, `rewardPerTokenStored` is returned unchanged. The `updateReward` modifier uses this value to checkpoint rewards, so any elapsed time with zero stakers produces zero reward accumulation — those reward tokens are silently stranded in the contract.

The `notifyRewardAmount()` function does guard against starting a reward period with zero stakers:

```solidity
if (totalKernelStaked == 0) revert NoStakedTokens();
``` [2](#0-1) 

However, this check only applies at the moment `notifyRewardAmount` is called. After that, any staker can call `initiateWithdrawal()` freely, which immediately decrements `totalKernelStaked`:

```solidity
balanceOf[msg.sender] -= _amount;
totalKernelStaked -= _amount;
``` [3](#0-2) 

If the last staker withdraws mid-period, `totalKernelStaked` hits zero and all remaining reward tokens for the rest of the period are permanently unclaimable. The contract itself acknowledges this in its NatSpec:

> *"If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards for that period will stay locked in the contract."* [4](#0-3) 

The stated mitigation is purely operational ("ensuring there are always some tokens staked"), with no on-chain enforcement. `KernelDepositPool` does not inherit `Recoverable` and has no admin sweep function for the `rewardsToken`. [5](#0-4) 

---

### Impact Explanation

Reward tokens transferred into the contract via `notifyRewardAmount` become permanently locked whenever `totalKernelStaked` drops to zero mid-period. There is no on-chain path to recover or redistribute them. This constitutes **permanent freezing of unclaimed yield**.

---

### Likelihood Explanation

Any single staker who holds the last staked tokens can trigger this by calling `initiateWithdrawal()`. No special role or privilege is required. The scenario is realistic: stakers may withdraw for legitimate reasons (market conditions, better opportunities) without awareness of the protocol-level consequence. The contract's own comment treats this as a known, unmitigated risk.

---

### Recommendation

Add an on-chain guard in `initiateWithdrawal()` that prevents `totalKernelStaked` from reaching zero while a reward period is active (`block.timestamp < finishAt`), or alternatively add an admin-callable recovery function (similar to the existing `Recoverable.sol` pattern in the codebase) that can sweep stranded `rewardsToken` balance after a reward period ends with zero stakers. [6](#0-5) 

---

### Proof of Concept

1. Admin calls `notifyRewardAmount(1_000e18)` while Alice has 100 KERNEL staked. `rewardRate` is set, `finishAt = block.timestamp + duration`. Reward tokens are transferred into the contract.
2. Alice calls `initiateWithdrawal(100)`. `totalKernelStaked` becomes 0. `balanceOf[Alice]` is zeroed out after the `updateReward` checkpoint captures her earned rewards up to this point.
3. Time passes. `rewardPerToken()` returns `rewardPerTokenStored` unchanged for the entire remaining period because `totalKernelStaked == 0`.
4. The reward tokens allocated for the remaining period (e.g., `rewardRate * remainingSeconds`) are never distributed to any user.
5. No function exists to recover these tokens. They remain locked in `KernelDepositPool` permanently. [7](#0-6)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L18-22)
```text
 * @dev If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards
 *      for that period will stay locked in the contract. In this deployment, we're avoiding this issue by
 *      ensuring there are always some tokens staked before admin calls the `notifyRewardAmount` function,
 *      as well as for the entire duration of the reward period. Otherwise, staking just 1 wei by any address
 *      will ensure that the contract never has any unallocated rewards.
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L24-24)
```text
contract KernelDepositPool is Initializable, AccessControlUpgradeable, ReentrancyGuardUpgradeable {
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L325-326)
```text
        balanceOf[msg.sender] -= _amount;
        totalKernelStaked -= _amount;
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

**File:** contracts/KERNEL/KernelDepositPool.sol (L566-591)
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
```

**File:** contracts/utils/Recoverable.sol (L41-57)
```text
    function recoverTokens(
        address tokenAddress,
        address recipient,
        uint256 amount
    )
        external
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        UtilLib.checkNonZeroAddress(tokenAddress);
        UtilLib.checkNonZeroAddress(recipient);
        if (amount == 0) revert ZeroAmount();
        if (IERC20(tokenAddress).balanceOf(address(this)) < amount) revert InsufficientBalance();

        IERC20(tokenAddress).safeTransfer(recipient, amount);

        emit TokensRecovered(tokenAddress, recipient, amount);
    }
```
