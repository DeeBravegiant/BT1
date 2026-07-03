Audit Report

## Title
FIFO Withdrawal Queue Blocked by Oversized Requests Freezes Subsequent User Withdrawals - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`_unlockWithdrawalRequests` iterates the withdrawal queue in strict FIFO order and unconditionally `break`s at line 800 when the vault's liquid balance cannot cover the front-of-queue request. Because `initiateWithdrawal` permits queuing requests sized against the full protocol TVL (including EigenLayer-held assets) while `unlockQueue` only sees the `LRTUnstakingVault`'s current liquid balance, a single large-but-legitimately-queued request can sit at `nextLockedNonce` and block all subsequent requests—including arbitrarily small ones—until operators manually unstake from EigenLayer and replenish the vault.

## Finding Description
**Root cause — structural liquidity gap between admission and processing:**

`initiateWithdrawal` (line 170) admits a request if `expectedAssetAmount <= getAvailableAssetAmount(asset)`. `getAvailableAssetAmount` (lines 599–603) computes availability as `lrtDepositPool.getTotalAssetDeposits(asset) - assetsCommitted[asset]`, which includes assets held in EigenLayer strategies.

`_createUnlockParams` (lines 846–850) supplies `totalAvailableAssets = unstakingVault.balanceOf(asset)` — only the vault's current liquid balance, which is typically a small fraction of total TVL.

**Queue processing — unconditional break:**

`_unlockWithdrawalRequests` (lines 790–815) iterates from `nextLockedNonce[asset]` to `firstExcludedIndex`. At line 800:

```solidity
if (availableAssetAmount < payoutAmount) break;
```

There is no `continue` path, no skip mechanism, and no way for the operator to advance past the blocking nonce. Line 788 reverts if `nextLockedNonce_ >= firstExcludedIndex`, so `firstExcludedIndex` cannot be used to skip the head of the queue. Line 815 writes `nextLockedNonce[asset] = nextLockedNonce_` only after the loop, so the pointer never advances past the blocking request.

**Exploit path:**
1. Vault holds 100 ETH liquid; EigenLayer holds 9,900 ETH. `getTotalAssetDeposits` returns 10,000 ETH.
2. Alice calls `initiateWithdrawal` for rsETH worth 500 ETH. Line 170 passes (500 < 10,000 − committed). Alice's request is queued at nonce 0.
3. Bob calls `initiateWithdrawal` for rsETH worth 1 ETH. Bob's request is queued at nonce 1.
4. Operator calls `unlockQueue(ETH, 2, ...)`. `_createUnlockParams` sets `totalAvailableAssets = 100 ETH`.
5. Loop iteration (nonce 0): `payoutAmount = 500 ETH > 100 ETH` → `break`. `nextLockedNonce[ETH]` stays at 0.
6. Bob's 1 ETH request (nonce 1) is never evaluated. Bob's rsETH remains locked in `LRTWithdrawalManager` (transferred in at line 166) until operators unstake ≥400 ETH from EigenLayer (subject to EigenLayer's 7-day withdrawal delay).

**Existing guards are insufficient:** The `firstExcludedIndex` upper-bound check and the `NoPendingWithdrawals` revert both operate on the queue range, not on individual request skipping. No guard prevents the blocking scenario.

## Impact Explanation
Users queued behind a large blocking request have their rsETH locked in `LRTWithdrawalManager` and cannot complete withdrawal. The freeze duration is bounded by EigenLayer's unstaking delay (typically 7+ days) plus operator response time. This is a concrete, temporary freezing of user funds matching the allowed impact: **Medium — Temporary freezing of funds**.

## Likelihood Explanation
Any unprivileged user holding sufficient rsETH can trigger this by calling `initiateWithdrawal` with a large amount when the vault's liquid balance is low relative to total TVL — a normal operational state for a protocol that deploys assets to EigenLayer. No privileged access, oracle manipulation, or external compromise is required. A single such request is sufficient to block the entire queue for all subsequent users. The condition is structural and persistent.

**Likelihood: Medium.**

## Recommendation
Replace the unconditional `break` at line 800 with a `continue` so the loop skips requests that cannot currently be covered and processes smaller subsequent requests. Alternatively, introduce an operator-callable mechanism to mark specific nonces as "deferred," allowing `nextLockedNonce` to advance past them. Either approach ensures the queue makes progress on what the vault can cover rather than halting entirely on what it cannot.

## Proof of Concept
**Foundry fork test outline:**

```solidity
// 1. Fork mainnet; ensure LRTUnstakingVault holds 100 ETH, EigenLayer holds 9,900 ETH.
// 2. Alice: initiateWithdrawal(ETH, rsETH_500ETH, 0) → nonce 0, passes line 170.
// 3. Bob:   initiateWithdrawal(ETH, rsETH_1ETH,   0) → nonce 1, passes line 170.
// 4. Advance blocks past withdrawalDelayBlocks.
// 5. Operator: unlockQueue(ETH, 2, prices...).
// 6. Assert nextLockedNonce[ETH] == 0 (unchanged).
// 7. Assert Bob's WithdrawalRequest.expectedAssetAmount unchanged (not unlocked).
// 8. Assert Bob cannot call completeWithdrawal (request not in unlocked state).
```

The test directly demonstrates that Bob's 1 ETH request — fully coverable by the 100 ETH vault balance — is never processed because Alice's 500 ETH request at nonce 0 causes the unconditional `break` at line 800.