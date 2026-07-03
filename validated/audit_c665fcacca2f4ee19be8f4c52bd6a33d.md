### Title
Missing `kernelToken != rewardsToken` Validation Allows Staking Pool Drain via Reward Claims - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
`KernelDepositPool.initialize()` accepts `_kernelToken` (staking token) and `_rewardToken` (rewards token) but never checks that they are distinct addresses. If both are set to the same token, reward claimants drain from the same ERC-20 balance that backs staked principal, causing other stakers' `claimWithdrawal` calls to fail.

### Finding Description
In `KernelDepositPool.initialize`, the contract validates that both `_kernelToken` and `_rewardToken` are non-zero, but imposes no constraint that they differ:

```solidity
function initialize(address _admin, address _kernelToken, address _rewardToken) external initializer {
    UtilLib.checkNonZeroAddress(_admin);
    UtilLib.checkNonZeroAddress(_kernelToken);
    UtilLib.checkNonZeroAddress(_rewardToken);
    // ← no require(_kernelToken != _rewardToken)
    kernelToken = IERC20(_kernelToken);
    rewardsToken = IERC20(_rewardToken);
}
``` [1](#0-0) 

When `kernelToken == rewardsToken`, the contract holds a single ERC-20 balance that simultaneously represents staked principal (tracked by `totalKernelStaked` / `balanceOf`) and reward tokens (funded via `notifyRewardAmount`). The two accounting systems are completely independent — neither `getReward` nor `claimWithdrawal` checks whether the contract holds enough tokens to satisfy both obligations.

`getReward` transfers `rewardsToken` directly from the contract's balance: [2](#0-1) 

`claimWithdrawal` transfers `kernelToken` from the same balance: [3](#0-2) 

Because both transfers draw from the same pool, reward claims reduce the balance available for principal withdrawals.

### Impact Explanation
If `kernelToken == rewardsToken`, every `getReward()` call reduces the contract's token balance below `totalKernelStaked`. Once the deficit is large enough, `claimWithdrawal` reverts with an ERC-20 insufficient-balance error for honest stakers who have already waited through the withdrawal delay. Their principal is frozen in the contract with no recovery path, because the accounting variables (`balanceOf`, `totalKernelStaked`) still show the correct staked amounts but the actual token balance no longer backs them.

**Impact: Permanent freezing of funds (Critical)** — stakers lose access to their principal.

### Likelihood Explanation
The `initialize` function is called once at deployment by the deployer/admin. A deployment script that passes the same token address for both parameters (e.g., using KERNEL as both staking and reward token, which is a common pattern in single-token staking systems) would silently create this misconfiguration. No on-chain guard prevents it. Once deployed and staked, the damage is irreversible.

### Recommendation
Add an equality check in `initialize`:

```solidity
require(_kernelToken != _rewardToken, "staking and reward tokens must differ");
``` [1](#0-0) 

### Proof of Concept
1. Deploy `KernelDepositPool` with `_kernelToken = _rewardToken = address(KERNEL)`.
2. Admin calls `setRewardsDuration(7 days)`.
3. Alice calls `stake(1000e18)` — contract holds 1000 KERNEL, `totalKernelStaked = 1000e18`.
4. Admin calls `notifyRewardAmount(100e18)` — transfers 100 KERNEL in; contract holds 1100 KERNEL, `rewardRate` set.
5. After the reward period, Alice calls `getReward()` — 100 KERNEL transferred out; contract holds 1000 KERNEL.
6. Bob calls `stake(500e18)` — contract holds 1500 KERNEL, `totalKernelStaked = 1500e18`.
7. Admin calls `notifyRewardAmount(200e18)` — contract holds 1700 KERNEL.
8. Alice calls `getReward()` again (accrued ~66 KERNEL proportionally) — contract holds ~1634 KERNEL.
9. Alice calls `initiateWithdrawal(1000e18)` then `claimWithdrawal` — succeeds, contract holds ~634 KERNEL.
10. Bob calls `initiateWithdrawal(500e18)` then `claimWithdrawal` — **reverts**: contract only holds ~634 KERNEL but needs 500 KERNEL for Bob plus remaining rewards. As more reward claims occur, Bob's principal becomes permanently frozen. [4](#0-3)

### Citations

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L375-377)
```text
        // Transfer the KERNEL tokens from contract to user
        kernelToken.safeTransfer(msg.sender, withdrawal.amount);

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
