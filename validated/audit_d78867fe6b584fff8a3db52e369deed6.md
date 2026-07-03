Audit Report

## Title
Reward Tokens Permanently Locked When `totalKernelStaked` Drops to Zero During Active Reward Period - (File: contracts/KERNEL/KernelDepositPool.sol)

## Summary
`KernelDepositPool` implements a Synthetix-style staking rewards mechanism where `rewardPerToken()` freezes at `rewardPerTokenStored` whenever `totalKernelStaked == 0`, causing reward emissions during that interval to be attributed to no one. Because no admin recovery function exists, any reward tokens emitted during a zero-staked interval are permanently irrecoverable. Any staker can trigger this condition unilaterally by calling `initiateWithdrawal` during an active reward period.

## Finding Description
`notifyRewardAmount` (L566) sets `rewardRate` and transfers reward tokens into the contract for the full `duration`. During the active period, any staker may call `initiateWithdrawal` (L320), which immediately decrements both `balanceOf[msg.sender]` and `totalKernelStaked` (L325-326) before the withdrawal delay begins. If this drives `totalKernelStaked` to zero, `rewardPerToken()` (L408-413) returns the frozen `rewardPerTokenStored` for every subsequent block until someone stakes again. The `rewardRate` continues to tick, but no address accumulates any share of those emissions. The `updateReward` modifier (L232-242) snapshots `rewardPerTokenStored` and `updatedAt` on each call, but with `totalKernelStaked == 0` the stored value never advances. The entire admin surface (L544-621) consists of `setRewardsDuration`, `notifyRewardAmount`, `setWithdrawalDelay`, and `setMaxNumberOfWithdrawalsPerUser` — none of which can retrieve stranded reward tokens. When a subsequent `notifyRewardAmount` call is made, the `remaining` calculation (L582) uses `(finishAt - block.timestamp) * rewardRate`, a rate-based projection that does not account for the actual token surplus already sitting in the contract from the zero-staked interval; those tokens are silently excluded from the new `rewardRate` and remain permanently locked. The contract's own NatSpec (L17-22) explicitly acknowledges this gap and defers entirely to off-chain operational controls, providing no on-chain safeguard.

## Impact Explanation
**Medium — Permanent freezing of unclaimed yield.** Reward tokens transferred into the contract for a distribution period that includes any zero-staked interval are permanently locked. They cannot be claimed by any user (no staker was present to earn them) and cannot be recovered by the admin (no recovery function exists). This maps directly to the allowed impact: *Medium. Permanent freezing of unclaimed yield.* The magnitude scales with `rewardRate × duration_of_zero_staked_interval`.

## Likelihood Explanation
The condition is reachable by any unprivileged staker through the normal `initiateWithdrawal` flow. A single large staker holding the majority of `totalKernelStaked` can drain the pool to zero at any time during an active reward period. The withdrawal delay (`withdrawalDelay`, up to 30 days) does not prevent `totalKernelStaked` from reaching zero — it only delays the final token transfer; the balance decrement is immediate (L325-326). The `notifyRewardAmount` guard `if (totalKernelStaked == 0) revert NoStakedTokens()` (L570) only blocks starting a new campaign with zero stakers and does nothing to protect rewards already in flight. Market-driven coordinated exits (competing yield, token price drop) make this scenario realistic without any coordination or privileged access.

## Recommendation
Add an admin-callable `recoverExcessRewards(address _treasury)` function that computes the difference between the contract's actual `rewardsToken` balance and the sum of all legitimately owed rewards (`rewardRate * (finishAt - block.timestamp) + Σ rewards[user]`), and transfers the surplus to a designated treasury address. Alternatively, when `totalKernelStaked` drops to zero mid-period inside `initiateWithdrawal`, snapshot the undistributed amount and pause the `rewardRate` (set it to zero and store the remaining balance), allowing the admin to roll it into the next campaign explicitly via `notifyRewardAmount`.

## Proof of Concept
1. Admin calls `notifyRewardAmount(1_000_000e18)` with `duration = 30 days`. `rewardRate ≈ 385e18/s`. Contract holds `1_000_000e18` reward tokens.
2. Alice is the only staker: `balanceOf[Alice] = 1000e18`, `totalKernelStaked = 1000e18`.
3. On day 1, Alice calls `initiateWithdrawal(1000e18)`. `totalKernelStaked` becomes `0` immediately (L326). Alice's withdrawal is queued with `unlockTime = block.timestamp + withdrawalDelay`.
4. For the remaining 29 days, every call to `rewardPerToken()` returns the frozen `rewardPerTokenStored` (L409-410). Approximately `385e18 × 29 days ≈ 966_000e18` reward tokens are emitted by the rate but attributed to nobody.
5. After `finishAt`, the contract holds ≈`966_000e18` reward tokens that no address can claim and no function can retrieve — permanently locked.
6. If the admin attempts a new campaign, `notifyRewardAmount` either reverts with `NoStakedTokens` (if `totalKernelStaked` is still zero) or proceeds with a new `rewardRate` computed only from the newly transferred amount plus the rate-based `remaining` projection — the stranded `966_000e18` tokens are not rolled forward and remain locked regardless (L579-584).

**Foundry test sketch:**
```solidity
function test_rewardsLockedOnZeroStake() public {
    // Setup: mint and stake
    kernelToken.mint(alice, 1000e18);
    vm.prank(alice); kernelToken.approve(address(pool), 1000e18);
    vm.prank(alice); pool.stake(1000e18);

    // Admin starts reward period
    rewardsToken.mint(admin, 1_000_000e18);
    vm.prank(admin); rewardsToken.approve(address(pool), 1_000_000e18);
    vm.prank(admin); pool.notifyRewardAmount(1_000_000e18);

    // Alice withdraws on day 1 — totalKernelStaked drops to 0
    vm.warp(block.timestamp + 1 days);
    vm.prank(alice); pool.initiateWithdrawal(1000e18);
    assertEq(pool.totalKernelStaked(), 0);

    // Fast-forward to end of reward period
    vm.warp(block.timestamp + 29 days);

    // Alice earned only 1 day of rewards; ~966_000e18 are stranded
    uint256 aliceEarned = pool.earned(alice);
    uint256 contractBalance = rewardsToken.balanceOf(address(pool));
    // contractBalance >> aliceEarned, and no recovery function exists
    assertGt(contractBalance - aliceEarned, 900_000e18);
}
```