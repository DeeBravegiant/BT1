Audit Report

## Title
FIFO Queue Head Blocking Causes Temporary Freezing of All Pending Withdrawals - (File: `contracts/LRTWithdrawalManager.sol`)

## Summary
`_unlockWithdrawalRequests` processes the withdrawal queue in strict FIFO order and unconditionally `break`s when the head request's payout exceeds the vault's available balance. Any withdrawal request at the front of the queue that cannot be immediately satisfied blocks every subsequent request from being unlocked, regardless of their individual sizes. Users whose requests sit behind the blocking head have their rsETH locked in `LRTWithdrawalManager` with no path to `completeWithdrawal` until the vault accumulates sufficient assets to cover the blocking request.

## Finding Description
The loop in `_unlockWithdrawalRequests` (lines 790–815) iterates from `nextLockedNonce_` upward and exits unconditionally when a single request cannot be covered:

```solidity
// LRTWithdrawalManager.sol line 800
if (availableAssetAmount < payoutAmount) break;
```

Because `nextLockedNonce` is only advanced for successfully unlocked requests, the oversized request permanently occupies the head of the queue. The `unlockQueue` function only accepts a `firstExcludedIndex` upper-bound parameter — there is no mechanism to skip or bypass a specific nonce.

The root cause is a discrepancy between two different asset-availability checks:

- **Queue time** (`initiateWithdrawal`, line 170): uses `getAvailableAssetAmount`, which calls `lrtDepositPool.getTotalAssetDeposits(asset)` — the total protocol-wide accounting figure.
- **Unlock time** (`_createUnlockParams`, line 849): uses `unstakingVault.balanceOf(asset)` — only the raw balance of assets that have already completed EigenLayer withdrawal and been moved to the vault.

A large withdrawal can legitimately pass the deposit-pool check at queue time while the vault holds far less than the committed amount. When `unlockQueue` is subsequently called, the vault balance may cover all smaller requests but not the large one — yet the `break` fires on the large request first, preventing any smaller requests behind it from being processed.

## Impact Explanation
**Medium — Temporary freezing of funds.** All users whose withdrawal requests sit behind the oversized head request have their rsETH locked inside `LRTWithdrawalManager` (transferred in at `initiateWithdrawal`) and cannot call `completeWithdrawal` until `nextLockedNonce` advances past the blocking request. The freeze persists until the vault accumulates enough assets to cover the large request, which may require multiple EigenLayer withdrawal cycles. The freeze is temporary (not permanent), placing this squarely in the Medium impact tier.

## Likelihood Explanation
**Medium.** The condition arises naturally in normal protocol operation without any special privileges, front-running, or oracle manipulation. Any unprivileged user can call the public `initiateWithdrawal` function with a large rsETH amount. The deposit-pool check at queue time passes as long as total protocol assets are sufficient, even if the vault holds far less. Smaller users who queue after the whale are then frozen. The scenario is repeatable and requires no attacker coordination beyond a single large withdrawal call.

## Recommendation
Replace the unconditional `break` at line 800 with a `continue` (or equivalent skip logic) so that requests the vault cannot currently cover are skipped rather than halting the entire queue. `nextLockedNonce` should only be advanced for successfully processed entries; skipped entries should remain eligible for future `unlockQueue` calls. Alternatively, allow operators to pass an explicit skip-list of nonces to bypass temporarily unserviceable requests. A secondary mitigation is to align the queue-time availability check with the vault balance rather than the deposit-pool total, preventing oversized requests from being queued when the vault cannot service them.

## Proof of Concept
1. Protocol has 1000 ETH worth of stETH in total deposits; `unstakingVault` holds 100 stETH.
2. **Whale** calls `initiateWithdrawal(stETH, rsETH_for_900_stETH)`. Check at line 170 passes (`900 < 1000 − 0`). `assetsCommitted[stETH] = 900`. Whale's request is assigned nonce 0.
3. **Alice** calls `initiateWithdrawal(stETH, rsETH_for_10_stETH)`. Check passes (`10 < 1000 − 900 = 100`). `assetsCommitted[stETH] = 910`. Alice's request is assigned nonce 1.
4. Operator calls `unlockQueue(stETH, 2, ...)`. `totalAvailableAssets = unstakingVault.balanceOf(stETH) = 100`.
5. Loop iteration 0 (nonce 0, whale): `payoutAmount ≈ 900`. `100 < 900` → **`break`**. Loop exits. `nextLockedNonce` stays at 0.
6. Alice's request (nonce 1, needing only 10 stETH) is never reached. Her rsETH remains locked in the contract with no path to `completeWithdrawal` until the vault accumulates ≥ 900 stETH.

**Foundry test plan:** Deploy `LRTWithdrawalManager` on a local fork. Seed the deposit pool with 1000 stETH and the vault with 100 stETH. Call `initiateWithdrawal` as whale (900 stETH equivalent), then as Alice (10 stETH equivalent). Call `unlockQueue` with `firstExcludedIndex = 2`. Assert that `nextLockedNonce[stETH] == 0` and that Alice's request status remains locked. Then assert that Alice cannot call `completeWithdrawal`.