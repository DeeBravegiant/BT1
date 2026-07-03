### Title
Low-Decimal Reward Token Precision Loss Permanently Freezes Unclaimed Yield - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary

`KernelDepositPool` uses a Synthetix-style staking rewards mechanism where `rewardRate` is stored in raw token units and `rewardPerToken()` multiplies by a fixed `DECIMAL_PRECISION = 1e18`. When the configured `rewardsToken` has fewer than 18 decimals (e.g., USDC at 6 decimals), the per-block increment to `rewardPerTokenStored` rounds down to zero while `updatedAt` still advances, permanently destroying the rewards for that time window.

### Finding Description

`rewardRate` is set in `notifyRewardAmount()` as raw token units divided by duration, with no upscaling:

```solidity
rewardRate = receivedAmount / duration;
``` [1](#0-0) 

`rewardPerToken()` then computes the per-token accumulator as:

```solidity
return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
    / totalKernelStaked;
``` [2](#0-1) 

The `updateReward` modifier, applied to every state-changing user function (`stake`, `withdraw`, `getReward`), snapshots both `rewardPerTokenStored` and `updatedAt` to the current block:

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();
    updatedAt = lastTimeRewardApplicable();
    ...
}
``` [3](#0-2) 

**Numerical example with USDC (6 decimals):**

- Reward: 1,209.6 USDC → `receivedAmount = 1_209_600_000`
- Duration: 1 week (604,800 s) → `rewardRate = 1_209_600_000 / 604_800 = 2000`
- `totalKernelStaked = 1_000_000e18`
- Per block (2 s): `2000 * 2 * 1e18 / 1_000_000e18 = 4_000 / 1_000_000 = 0`

`rewardPerTokenStored` does not increase, but `updatedAt` advances. The rewards for that 2-second window are permanently unrecoverable.

An unprivileged attacker can guarantee this by calling `stake(1)` every block (or every N blocks where N is still small enough to round to zero). Even without active griefing, any organic user interaction (staking, withdrawing, claiming) at a frequency above the rounding threshold silently destroys yield.

### Impact Explanation

**High — Permanent freezing of unclaimed yield.** KERNEL stakers receive zero rewards in any low-decimal token (USDC, USDT) distributed through this contract. The reward tokens remain locked in the contract with no recovery path for stakers.

### Likelihood Explanation

**High.** The `rewardsToken` is set at initialization with no decimal restriction. [4](#0-3) 

USDC and USDT are the most common staking reward tokens in DeFi. Normal user activity (staking, withdrawing, claiming) is sufficient to trigger the rounding loss without any deliberate attack. A griefer with 1 wei of KERNEL can guarantee total loss by calling `stake(1)` each block. [5](#0-4) 

### Recommendation

Upscale `rewardRate` by a precision multiplier (e.g., `1e12`) at the time it is stored in `notifyRewardAmount`, and divide by the same multiplier when transferring rewards in `getReward`. This is the standard fix for Synthetix-fork contracts that support sub-18-decimal reward tokens.

Alternatively, enforce that `rewardsToken` must have exactly 18 decimals, or add a per-token decimal normalization factor.

### Proof of Concept

1. Admin calls `setRewardsDuration(604800)` (1 week).
2. Admin calls `notifyRewardAmount(1_209_600_000)` with USDC as `rewardsToken`.
   - `rewardRate = 1_209_600_000 / 604_800 = 2000`
3. `totalKernelStaked = 1_000_000e18` (realistic TVL).
4. Attacker (or any normal user) calls `stake(1)` every 2 seconds (each block).
   - Each call triggers `updateReward`: `rewardPerToken()` returns `rewardPerTokenStored + 0 = rewardPerTokenStored` (rounds to zero), but `updatedAt` advances by 2.
5. After 1 week, `rewardPerTokenStored` is still 0. All stakers call `getReward()` and receive 0 USDC.
6. 1,209.6 USDC is permanently frozen in the contract.

The threshold for rounding to zero without per-block griefing: any call interval shorter than `totalKernelStaked / (rewardRate * DECIMAL_PRECISION)` = `1_000_000e18 / (2000 * 1e18)` = 500 seconds (≈250 blocks) causes the same loss.

### Citations

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L579-584)
```text
        if (block.timestamp >= finishAt) {
            rewardRate = receivedAmount / duration;
        } else {
            uint256 remaining = (finishAt - block.timestamp) * rewardRate;
            rewardRate = (receivedAmount + remaining) / duration;
        }
```
