Audit Report

## Title
Sequential `break` in `_unlockWithdrawalRequests` Permanently Blocks Later Queue Entries When a Large Request Cannot Be Satisfied - (File: contracts/LRTWithdrawalManager.sol)

## Summary

`_unlockWithdrawalRequests` processes withdrawal requests in strict nonce order and `break`s immediately when any single request's payout exceeds `availableAssetAmount`. Because `nextLockedNonce` is only advanced past requests that are successfully unlocked, every subsequent request — regardless of size — is permanently gated behind the unsatisfiable one. There is no cancel path, so affected users' rsETH is locked in the contract with no recourse until the blocking request can be satisfied.

## Finding Description

The loop in `_unlockWithdrawalRequests` (lines 790–815) iterates from `nextLockedNonce[asset]` upward:

```solidity
while (nextLockedNonce_ < firstExcludedIndex) {
    ...
    if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;
    uint256 payoutAmount = _calculatePayoutAmount(request, rsETHPrice, assetPrice);
    if (availableAssetAmount < payoutAmount) break; // line 800
    ...
    unchecked { nextLockedNonce_++; }
}
nextLockedNonce[asset] = nextLockedNonce_;  // line 815
```

When the condition at line 800 fires, the loop exits without incrementing `nextLockedNonce_`, so `nextLockedNonce[asset]` is written back at the position of the blocking request. `_processWithdrawalCompletion` (called by `completeWithdrawal`) enforces:

```solidity
if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked(); // line 707
```

Any user whose nonce is ≥ the stuck nonce is permanently blocked from completing their withdrawal. There is no `cancel` function anywhere in `LRTWithdrawalManager.sol` (confirmed by search), so users have no escape path. rsETH is transferred into the contract at `initiateWithdrawal` (line 166) and cannot be recovered until the blocking request is eventually satisfied.

**Concrete exploit path (no adversary required):**
1. User A calls `initiateWithdrawal` for a large rsETH amount → assigned nonce `N`.
2. Users B, C, D call `initiateWithdrawal` for small amounts → nonces `N+1`, `N+2`, `N+3`.
3. Operator calls `unlockQueue` with `availableAssetAmount` sufficient for B, C, D but not A.
4. Loop reaches nonce `N`, computes `payoutAmount > availableAssetAmount`, `break`s.
5. `nextLockedNonce[asset]` remains `N`.
6. B, C, D call `completeWithdrawal`; all revert `WithdrawalLocked` because nonces `N+1`–`N+3` ≥ `N`.
7. B, C, D's rsETH is locked indefinitely.

## Impact Explanation

Users B, C, D have their rsETH locked in `LRTWithdrawalManager` with no mechanism to recover it. This is a concrete **temporary (potentially permanent) freezing of funds**. It escalates toward **permanent freezing** if User A's request is large enough that the protocol can never accumulate sufficient assets to satisfy it in a single `unlockQueue` call. Both impact tiers are in scope.

## Likelihood Explanation

No adversarial action is required. The conditions — a large withdrawal queued before smaller ones, with the protocol's available assets insufficient to cover the large one at unlock time — are routine in a liquid restaking protocol where assets are deployed to EigenLayer and only partially liquid at any given moment. Any unprivileged user can trigger this by submitting a large `initiateWithdrawal` before others submit smaller ones. The scenario is repeatable and organic.

## Recommendation

Replace the `break` at line 800 with a `continue` so the loop skips unsatisfiable requests and attempts subsequent ones:

```solidity
if (availableAssetAmount < payoutAmount) {
    unchecked { nextLockedNonce_++; }
    continue;
}
```

Note: skipping a request means its `assetsCommitted` entry must remain committed (or be explicitly released) to avoid double-counting. Alternatively, introduce a user-callable `cancelWithdrawal` that allows users to reclaim their rsETH from pending (not-yet-unlocked) requests, providing an escape path independent of queue ordering.

## Proof of Concept

**Foundry test outline:**

```solidity
// 1. Deploy LRTWithdrawalManager with a supported asset (e.g. stETH).
// 2. User A: initiateWithdrawal(stETH, largeRsETH)  → nonce 0
// 3. User B: initiateWithdrawal(stETH, smallRsETH)  → nonce 1
// 4. Advance block.number past withdrawalDelayBlocks.
// 5. Operator: unlockQueue(stETH, smallAmount, upperLimit=2)
//    where smallAmount >= B's payoutAmount but < A's payoutAmount.
// 6. Assert: nextLockedNonce[stETH] == 0  (stuck at A's nonce)
// 7. vm.prank(userB); completeWithdrawal(stETH, "")
//    → expect revert WithdrawalLocked()
// 8. Assert userB's rsETH balance unchanged (still locked in contract).
```

The root cause line is confirmed at [1](#0-0)  — the `break` that halts the loop without advancing the nonce cursor. The cursor write-back is at [2](#0-1) . The gate blocking downstream users is at [3](#0-2) . rsETH is locked at deposit with no cancel path at [4](#0-3) , and nonces are assigned sequentially at [5](#0-4) .

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-166)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L707-707)
```text
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
```

**File:** contracts/LRTWithdrawalManager.sol (L755-757)
```text
        // Map the user to the newly created request index and increment the nonce for future requests.
        userAssociatedNonces[asset][msg.sender].pushBack(nextUnusedNonce_);
        nextUnusedNonce[asset] = nextUnusedNonce_ + 1;
```

**File:** contracts/LRTWithdrawalManager.sol (L800-800)
```text
            if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request
```

**File:** contracts/LRTWithdrawalManager.sol (L815-815)
```text
        nextLockedNonce[asset] = nextLockedNonce_;
```
