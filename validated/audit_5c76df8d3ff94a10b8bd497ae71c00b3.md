Audit Report

## Title
`initiateWithdrawal` Availability Guard Uses Aggregate Protocol Assets While `unlockQueue` Can Only Draw From `LRTUnstakingVault` — (`File: contracts/LRTWithdrawalManager.sol`)

## Summary

`initiateWithdrawal` gates new requests using `getAvailableAssetAmount`, which calls `LRTDepositPool.getTotalAssetDeposits` and sums assets across all protocol locations including illiquid EigenLayer-staked and EigenLayer-unstaking buckets. `unlockQueue`, the only function that marks requests as serviceable, exclusively draws from `unstakingVault.balanceOf(asset)`. Because the protocol's normal steady-state keeps the majority of assets restaked in EigenLayer, the initiation guard is systematically over-optimistic, and users' rsETH is locked in `LRTWithdrawalManager` with no recourse until operators complete a multi-step, time-delayed EigenLayer unstaking cycle.

## Finding Description

**Root cause — mismatched liquidity accounting between initiation and unlock.**

`getAvailableAssetAmount` (L599–603) delegates to `getTotalAssetDeposits`, which sums six buckets:

```
assetLyingInDepositPool + assetLyingInNDCs + assetStakedInEigenLayer
  + assetUnstakingFromEigenLayer + assetLyingInConverter + assetLyingUnstakingVault
```

`assetStakedInEigenLayer` and `assetUnstakingFromEigenLayer` are not liquid — they require `NodeDelegator.initiateUnstaking` + EigenLayer's ≥7-day delay + `completeUnstaking` before they reach the vault.

`_createUnlockParams` (L837–851) sets `totalAvailableAssets` to only `unstakingVault.balanceOf(asset)`. `unlockQueue` passes this single-provider figure into `_unlockWithdrawalRequests` and redeems exclusively from the vault (L307).

**Exploit path:**

1. Protocol holds 1 000 ETH: 950 ETH staked in EigenLayer via NodeDelegators, 50 ETH in the unstaking vault.
2. `getAvailableAssetAmount(ETH)` returns `1 000 − 0 = 1 000 ETH`.
3. Alice calls `initiateWithdrawal(ETH, rsETHFor900ETH, ...)`. Guard passes (900 < 1 000). Her rsETH is transferred to `LRTWithdrawalManager` at L166. `assetsCommitted[ETH] += 900 ETH`.
4. Operator calls `unlockQueue`. `_createUnlockParams` sets `totalAvailableAssets = unstakingVault.balanceOf(ETH) = 50 ETH`. Only 50 ETH of requests can be unlocked; Alice's 900 ETH request cannot proceed.
5. To service Alice, the operator must call `NodeDelegator.initiateUnstaking`, wait ≥7 days, call `completeUnstaking`, then call `unlockQueue` again.
6. Alice's rsETH remains locked in `LRTWithdrawalManager` for the entire duration. No cancel or reclaim function exists for users.

**Why existing checks fail:** The `ExceedAmountToWithdraw` guard at L170 is the only protection, but it uses the wrong liquidity figure. There is no mechanism to cancel a queued withdrawal request and recover rsETH.

## Impact Explanation

Any user who calls `initiateWithdrawal` when the majority of protocol assets are in EigenLayer strategies — the protocol's normal operating state — will have their rsETH frozen in `LRTWithdrawalManager` for at least the EigenLayer withdrawal delay (≥7 days) plus operator latency, even though the availability guard reported sufficient capacity. This is a **temporary freezing of user funds** (Medium), matching the allowed impact scope.

## Likelihood Explanation

The protocol's core purpose is restaking assets in EigenLayer. Therefore, the vast majority of assets will routinely reside in EigenLayer strategies, making the mismatch between the multi-provider availability check and the single-provider unlock path a near-constant condition, not an edge case. Any unprivileged user can trigger this by calling `initiateWithdrawal` with any non-trivial amount.

## Recommendation

**Short term:** Replace the `getAvailableAssetAmount` guard in `initiateWithdrawal` with a check against `unstakingVault.balanceOf(asset)` (or a dedicated "liquid reserve" figure) so the guard reflects what `unlockQueue` can actually service immediately.

**Long term:** Introduce a unified "available-for-withdrawal" accounting layer that tracks only assets that have already reached the unstaking vault, and enforce that `assetsCommitted` never exceeds this figure. Alternatively, add a user-callable cancellation path that returns locked rsETH if the request has not yet been unlocked.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// Fork test outline (Foundry):
// 1. Fork mainnet at a block where protocol has significant EigenLayer deposits.
// 2. Assert getTotalAssetDeposits(ETH) >> unstakingVault.balance (ETH).
// 3. Alice approves and calls initiateWithdrawal(ETH, rsETHFor900ETH, "").
//    - Confirm tx succeeds (guard passes using aggregate figure).
//    - Confirm Alice's rsETH balance decreased by rsETHFor900ETH.
//    - Confirm assetsCommitted[ETH] increased by ~900 ETH.
// 4. Call unlockQueue(ETH, ...) as operator.
//    - Confirm totalAvailableAssets == unstakingVault.balance == 50 ETH.
//    - Confirm Alice's request is NOT unlocked (nextLockedNonce unchanged for her nonce).
// 5. Confirm Alice has no cancel/reclaim function available.
// 6. Advance time by 7+ days, simulate NodeDelegator.initiateUnstaking + completeUnstaking,
//    confirm only then can unlockQueue service Alice's request.
```