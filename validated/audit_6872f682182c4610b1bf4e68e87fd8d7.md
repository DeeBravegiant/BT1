Audit Report

## Title
Race Condition in `processChunk` / `finalizeInitialize2` Inflates `unlockedWithdrawalsCount`, Permanently Blocking `sweepRemainingAssets` — (`contracts/utils/UnlockedWithdrawalsInitializer.sol`)

## Summary

`processChunk` snapshots `expectedAssetAmount > 0` at call time and accumulates the count in `unlockedCount[asset]`. If a user calls `completeWithdrawal` for any already-counted request before `finalizeInitialize2` executes, that request is deleted from `withdrawalRequests` but `unlockedCount` is never corrected. `finalizeInitialize2` then passes the stale, inflated value to `initialize2`, which overwrites the live `unlockedWithdrawalsCount`. After all real withdrawals drain the counter, it permanently sits at 1, causing `hasUnlockedWithdrawals` to return `true` forever and `sweepRemainingAssets` to revert with `PendingWithdrawalsExist` on every call.

## Finding Description

**Root cause — `processChunk` counts live state without a liveness guard:** [1](#0-0) 

For every nonce `i` in `[start, limit)`, the function reads `withdrawalRequests[requestId].expectedAssetAmount`. If non-zero, the request is counted and `unlockedCount[asset]` is incremented. This value is stored permanently in the initializer's storage.

**`completeWithdrawal` deletes the request and decrements the live counter, but never touches `unlockedCount`:** [2](#0-1) 

`delete withdrawalRequests[requestId]` zeroes `expectedAssetAmount`. The live counter `unlockedWithdrawalsCount[asset]` is decremented. The initializer's `unlockedCount[asset]` is untouched.

**`finalizeInitialize2` blindly passes the stale accumulated count to `initialize2`:** [3](#0-2) 

**`initialize2` is a `reinitializer(2)` — it can only be called once and overwrites the live counter:** [4](#0-3) 

**`sweepRemainingAssets` is permanently gated on `hasUnlockedWithdrawals`:** [5](#0-4) [6](#0-5) 

**`isAssetComplete` only checks `processedIndex[asset] >= nextLockedNonce[asset]`:** [7](#0-6) 

`completeWithdrawal` does not modify `nextLockedNonce`, so `isAssetComplete` returns `true` even after a withdrawal is completed during the window. The `PendingWithdrawalsExist` guard in `finalizeInitialize2` does not detect the staleness.

**Exploit flow:**
1. N requests are unlocked for asset A; `unlockedWithdrawalsCount[A]` = N.
2. Operator calls `processChunk(A, N)` → `unlockedCount[A]` = N, `processedIndex[A]` = N.
3. User calls `completeWithdrawal(A)` → request deleted, `unlockedWithdrawalsCount[A]` = N−1. `unlockedCount[A]` remains N.
4. Manager calls `finalizeInitialize2()` → `isAssetComplete` returns true (nextLockedNonce unchanged) → `initialize2(N, ...)` → `unlockedWithdrawalsCount[A]` = N (stale value written).
5. Only N−1 real withdrawals remain. After all complete: `unlockedWithdrawalsCount[A]` = N − (N−1) = 1.
6. `hasUnlockedWithdrawals(A)` returns `true` forever. `sweepRemainingAssets(A)` reverts on every call.

## Impact Explanation

The remaining balance in `LRTWithdrawalManager` (excess assets accumulated from price movements between withdrawal initiation and completion) is permanently frozen in the contract. `sweepRemainingAssets` is the only non-upgrade mechanism to recover these funds, and it is permanently blocked. No non-upgrade path exists to correct `unlockedWithdrawalsCount` because `initialize2` is a `reinitializer(2)` and can only be called once. This matches **Critical — Permanent freezing of funds**.

## Likelihood Explanation

The initialization window is not atomic. `processChunk` is called by an operator in one or more transactions; `finalizeInitialize2` is called by a manager in a separate transaction. The gap can span many blocks. During this window, `completeWithdrawal` is open to any user whose withdrawal delay (8 days / 12 seconds ≈ 57,600 blocks) has passed — a normal, expected user action requiring no special role, front-running, or key compromise. The condition is reachable whenever at least one user completes a withdrawal during the initialization window, which is likely on a live deployment with existing unlocked requests.

## Recommendation

1. **Recount at finalization time**: replace the stale `unlockedCount` read in `finalizeInitialize2` with a fresh call to `getUnlockedWithdrawalsCount` (which re-reads live `withdrawalRequests` state) for each asset before passing values to `initialize2`.
2. **Enforce atomicity**: require the contract to be paused during the entire `processChunk` → `finalizeInitialize2` sequence, enforced by a `whenPaused` modifier on both functions.
3. **Alternatively**, track completions that occur during the window (e.g., by hooking `completeWithdrawal` to decrement `unlockedCount` if `isInitialized2()` is false) and subtract them before calling `initialize2`.

## Proof of Concept

```solidity
// Setup: 3 requests unlocked for asset A (nonces 0,1,2), nextLockedNonce=3
// unlockedWithdrawalsCount[A] = 3 (set by prior unlockQueue calls)

// Step 1: operator calls processChunk(A, 3)
//   → unlockedCount[A] = 3, processedIndex[A] = 3
//   → isAssetComplete(A) == true (processedIndex[3] >= nextLockedNonce[3])

// Step 2: user calls completeWithdrawal(A) for nonce 0
//   → delete withdrawalRequests[keccak(A,0)]  (expectedAssetAmount → 0)
//   → unlockedWithdrawalsCount[A] = 2
//   → unlockedCount[A] still = 3 (initializer unaware)
//   → nextLockedNonce[A] still = 3 (isAssetComplete still true)

// Step 3: manager calls finalizeInitialize2()
//   → isAssetComplete(A) == true  ← no staleness detected
//   → initialize2(3, ...) called
//   → unlockedWithdrawalsCount[A] = 3  (overwritten with stale value)

// Now only 2 real withdrawals remain (nonces 1,2).
// After users complete both: unlockedWithdrawalsCount[A] = 3 - 2 = 1
// hasUnlockedWithdrawals(A) == true  ← permanently
// sweepRemainingAssets(A) reverts forever with PendingWithdrawalsExist

assert(withdrawalManager.unlockedWithdrawalsCount(A) == 1);
assert(withdrawalManager.hasUnlockedWithdrawals(A) == true);
vm.expectRevert(LRTWithdrawalManager.PendingWithdrawalsExist.selector);
withdrawalManager.sweepRemainingAssets(A);
```

### Citations

**File:** contracts/utils/UnlockedWithdrawalsInitializer.sol (L97-106)
```text
        for (uint256 i = start; i < limit; i++) {
            bytes32 requestId = _withdrawalManager().getRequestId(asset, i);
            (, uint256 expectedAssetAmount,) = _withdrawalManager().withdrawalRequests(requestId);
            if (expectedAssetAmount > 0) {
                added++;
            }
        }
        unlockedCount[asset] += added;
        processed = limit - start;
        processedIndex[asset] = limit;
```

**File:** contracts/utils/UnlockedWithdrawalsInitializer.sol (L117-122)
```text
        uint256 countETHx = unlockedCount[_ethX()];
        uint256 countSTETH = unlockedCount[_stETH()];
        uint256 countETH = unlockedCount[_eth()];

        _withdrawalManager().initialize2(countETHx, countSTETH, countETH);
        emit Finalized(countETHx, countSTETH, countETH);
```

**File:** contracts/utils/UnlockedWithdrawalsInitializer.sol (L143-146)
```text
    function isAssetComplete(address asset) public view returns (bool) {
        uint256 target = _withdrawalManager().nextLockedNonce(asset);
        return processedIndex[asset] >= target;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L115-121)
```text
        reinitializer(2)
        onlyRole(LRTConstants.UNLOCKED_WITHDRAWAL_INITIALIZER)
    {
        unlockedWithdrawalsCount[lrtConfig.getLSTToken(LRTConstants.ST_ETH_TOKEN)] = unlockedWithdrawalsCountSTETH;
        unlockedWithdrawalsCount[lrtConfig.getLSTToken(LRTConstants.ETHX_TOKEN)] = unlockedWithdrawalsCountETHx;
        unlockedWithdrawalsCount[LRTConstants.ETH_TOKEN] = unlockedWithdrawalsCountETH;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L403-403)
```text
        if (hasUnlockedWithdrawals(asset)) revert PendingWithdrawalsExist();
```

**File:** contracts/LRTWithdrawalManager.sol (L629-631)
```text
    function hasUnlockedWithdrawals(address asset) public view returns (bool hasUnlocked) {
        return unlockedWithdrawalsCount[asset] > 0;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L712-717)
```text
        delete withdrawalRequests[requestId];

        // Check that the withdrawal delay has passed since the request's initiation.
        if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();

        unlockedWithdrawalsCount[asset]--;
```
