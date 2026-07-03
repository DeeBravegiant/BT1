Audit Report

## Title
Reward Tokens Permanently Frozen When `totalKernelStaked` Drops to Zero During Active Reward Period - (File: contracts/KERNEL/KernelDepositPool.sol)

## Summary
In `KernelDepositPool.sol`, the `updateReward` modifier unconditionally advances `updatedAt` to `lastTimeRewardApplicable()` even when `totalKernelStaked == 0`. Because `rewardPerToken()` returns the frozen `rewardPerTokenStored` during the zero-supply window, the elapsed time is silently consumed with no reward credit. Any reward tokens accrued during that window are permanently locked in the contract with no recovery path.

## Finding Description
The `updateReward` modifier at lines 232–242 always executes `updatedAt = lastTimeRewardApplicable()` regardless of `totalKernelStaked`:

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();   // frozen when totalKernelStaked == 0
    updatedAt = lastTimeRewardApplicable();    // always advances
    ...
}
```

`rewardPerToken()` at lines 408–413 returns `rewardPerTokenStored` unchanged when `totalKernelStaked == 0`, so no reward accumulation occurs. Yet `updatedAt` is advanced, permanently discarding the time gap.

The exploit path:
1. Admin calls `notifyRewardAmount()` with at least one staker present (passes the `NoStakedTokens` guard at line 570). `rewardRate`, `finishAt`, and `updatedAt` are set.
2. The last staker calls `initiateWithdrawal(fullBalance)`. The `updateReward` modifier runs first while `totalKernelStaked > 0`, correctly snapshotting rewards and setting `updatedAt = block.timestamp`. Then `totalKernelStaked -= _amount` reaches 0 (line 326).
3. Time elapses with `totalKernelStaked == 0`. Any subsequent call that triggers `updateReward` (e.g., `getReward()`, `stake()`) advances `updatedAt` to `lastTimeRewardApplicable()` while `rewardPerTokenStored` stays frozen.
4. When a new staker calls `stake()`, `updateReward` runs with `totalKernelStaked` still 0 at modifier time, consuming the entire zero-staked window into `updatedAt`. The staker's body then sets `totalKernelStaked > 0`, but the elapsed time is already gone.
5. Rewards for the zero-staked window (`rewardRate × elapsed_seconds`) are permanently locked. No user can claim them, and the contract has no rescue or sweep function for `rewardsToken` (confirmed: the contract ends at line 621 with no such function).

The `notifyRewardAmount` guard (`if (totalKernelStaked == 0) revert NoStakedTokens()`) only prevents starting a new period with zero stakers; it does not prevent `totalKernelStaked` from dropping to zero mid-period. The NatSpec at lines 18–22 acknowledges the issue but relies entirely on an off-chain operational promise with no on-chain enforcement.

## Impact Explanation
Reward tokens (`rewardsToken`) corresponding to the zero-staked window are permanently locked in `KernelDepositPool`. They cannot be claimed by any user and cannot be recovered by the admin. This is a concrete instance of **permanent freezing of unclaimed yield** (Medium), which is an explicitly allowed impact in scope.

## Likelihood Explanation
`initiateWithdrawal()` is an unprivileged, externally callable function. Any staker can call it at any time with no preconditions beyond having a staked balance. If the total staked supply is small (e.g., one or a few stakers), a single user withdrawing their full balance suffices to trigger the condition. The protocol's only mitigation is an off-chain operational assumption, which provides no on-chain guarantee and cannot prevent a user from withdrawing their own tokens.

## Recommendation
In the `updateReward` modifier, only advance `updatedAt` when `totalKernelStaked > 0`:

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();
    if (totalKernelStaked > 0) {
        updatedAt = lastTimeRewardApplicable();
    }
    if (_account != address(0)) {
        rewards[_account] = earned(_account);
        userRewardPerTokenPaid[_account] = rewardPerTokenStored;
    }
    _;
}
```

This ensures that time elapsed while no tokens are staked is not silently consumed, so rewards for that window remain distributable when stakers return.

## Proof of Concept
1. Deploy `KernelDepositPool`. Alice stakes `1000e18` tokens.
2. Admin calls `notifyRewardAmount(1000e18)`. `rewardRate = 1000e18 / duration`, `finishAt = T + duration`, `updatedAt = T`.
3. At `T + duration/2`, Alice calls `initiateWithdrawal(1000e18)`. `updateReward` runs (correctly snapshots Alice's rewards for `[T, T+duration/2]`), then `totalKernelStaked = 0`.
4. At `T + duration` (or any time before), Alice calls `getReward()`. `updateReward` runs: `rewardPerToken()` returns `rewardPerTokenStored` (frozen), `updatedAt` advances to `finishAt`. The `~500e18` tokens for `[T+duration/2, T+duration]` are now permanently unaccountable.
5. Bob calls `stake(1e18)` after step 4. `updateReward` runs with `totalKernelStaked == 0`; `updatedAt` is already at `finishAt`, so Bob earns nothing for the remaining period.
6. Verify: `rewardsToken.balanceOf(address(pool))` retains the frozen `~500e18` tokens indefinitely; no function in the contract can extract them.