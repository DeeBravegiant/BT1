### Title
`KernelDepositPool` Cannot Distribute Rebasing Token Yield When Used as `rewardsToken` - (File: `contracts/KERNEL/KernelDepositPool.sol`)

### Summary
`KernelDepositPool` uses a fixed `rewardRate` set at `notifyRewardAmount` time. If a rebasing ERC20 token (e.g., stETH) is configured as `rewardsToken`, any yield that accrues via automatic rebasing after the initial transfer is permanently stranded in the contract with no mechanism to distribute or recover it.

### Finding Description
`KernelDepositPool` is a Synthetix-style staking contract where users stake KERNEL tokens and earn a configurable `rewardsToken`. The reward distribution is entirely driven by an internal `rewardRate` variable set in `notifyRewardAmount`:

```solidity
uint256 balanceBefore = rewardsToken.balanceOf(address(this));
rewardsToken.safeTransferFrom(msg.sender, address(this), _amount);
uint256 balanceAfter = rewardsToken.balanceOf(address(this));
uint256 receivedAmount = balanceAfter - balanceBefore;

rewardRate = receivedAmount / duration;   // or rolling calculation
``` [1](#0-0) 

`rewardRate` is fixed at the moment of the call. All user reward accounting flows through this rate:

```solidity
return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
    / totalKernelStaked;
``` [2](#0-1) 

If `rewardsToken` is a rebasing token such as stETH, the token's balance held by the contract increases automatically over time (rebasing yield). This additional balance is never reflected in `rewardRate`, never included in `rewardPerTokenStored`, and never reachable via `getReward()`. There is no sweep, rescue, or secondary `notifyRewardAmount` path that would capture and redistribute the accrued rebasing yield. [3](#0-2) 

The `rewardsToken` is set at initialization with no type restriction:

```solidity
rewardsToken = IERC20(_rewardToken);
``` [4](#0-3) 

### Impact Explanation
Any rebasing yield that accrues on the `rewardsToken` balance held by `KernelDepositPool` is permanently unclaimable. It cannot be distributed to stakers (not in `rewardRate`), cannot be swept by the admin (no rescue function), and cannot be re-notified without a separate manual transfer. This constitutes **theft of unclaimed yield** — the yield belongs economically to the stakers but is permanently stranded in the contract.

**Impact: High** — Theft of unclaimed yield.

### Likelihood Explanation
The Kelp DAO / KERNEL protocol operates in the ETH liquid restaking space. stETH is the canonical rebasing LST and is already a first-class asset in the broader LRT-rsETH system (the `LRTDepositPool` explicitly stakes ETH for stETH via `stakeEthForStETH`). [5](#0-4) 

It is entirely plausible — and consistent with the protocol's design philosophy — that the admin would configure stETH (or another rebasing LST) as the `rewardsToken` for `KernelDepositPool`. No on-chain guard prevents this. The admin acts in good faith; the loss is a silent protocol design gap, not malice.

**Likelihood: Medium.**

### Recommendation
1. Restrict `rewardsToken` to non-rebasing tokens, or
2. Add a `collectRebasingYield()` function that measures the surplus between `rewardsToken.balanceOf(address(this))` and the internally tracked distributed amount, then feeds it back through `notifyRewardAmount`-equivalent logic, or
3. Document explicitly that rebasing tokens must not be used as `rewardsToken`.

### Proof of Concept
1. Admin calls `initialize(admin, kernelToken, stETH)` — `rewardsToken = stETH`.
2. Admin calls `notifyRewardAmount(1000e18)` — 1000 stETH transferred in; `rewardRate = 1000e18 / duration`.
3. 30 days pass. stETH rebases ~3% APY → contract now holds ≈1002.5 stETH.
4. All stakers call `getReward()` — they collectively receive exactly 1000 stETH (the `rewardRate`-derived amount).
5. The remaining ≈2.5 stETH rebasing yield sits in the contract forever with no path to claim it. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L62-66)
```text
    /// @notice The KERNEL token contract (used for staking)
    IERC20 public kernelToken;

    /// @notice The rewards token contract
    IERC20 public rewardsToken;
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L270-271)
```text
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

**File:** contracts/KERNEL/KernelDepositPool.sol (L408-413)
```text
    function rewardPerToken() public view returns (uint256) {
        if (totalKernelStaked == 0) {
            return rewardPerTokenStored;
        }
        return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
            / totalKernelStaked;
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

**File:** contracts/LRTDepositPool.sol (L565-571)
```text
    function stakeEthForStETH(address referral, uint256 ethAmount) external onlyLRTManager {
        address stETHAddress = lrtConfig.getLSTToken(LRTConstants.ST_ETH_TOKEN);

        uint256 stETHShares = ILido(stETHAddress).submit{ value: ethAmount }(referral);

        emit AssetStaked(stETHAddress, ethAmount, stETHShares);
    }
```
