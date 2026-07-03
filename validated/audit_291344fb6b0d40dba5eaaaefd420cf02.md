### Title
Race Condition in `processChunk` / `finalizeInitialize2` Inflates `unlockedWithdrawalsCount`, Permanently Blocking `sweepRemainingAssets` — (`contracts/utils/UnlockedWithdrawalsInitializer.sol`)

---

### Summary

`processChunk` snapshots `expectedAssetAmount > 0` at call time and accumulates the count in `unlockedCount`. If a user calls `completeWithdrawal` for any already-counted request before `finalizeInitialize2` is executed, that request is deleted from `withdrawalRequests` but `unlockedCount` is never corrected. `initialize2` then **sets** `unlockedWithdrawalsCount` to the stale, inflated value. After all real withdrawals drain the counter to 1, `hasUnlockedWithdrawals` permanently returns `true` and `sweepRemainingAssets` is permanently blocked.

---

### Finding Description

**`processChunk` — snapshot without liveness guard** [1](#0-0) 

For every nonce `i` in `[start, limit)`, the function reads `withdrawalRequests[requestId].expectedAssetAmount`. If it is non-zero the request is counted as unlocked and `unlockedCount[asset]` is incremented. The result is stored permanently in `unlockedCount[asset]`.

**`completeWithdrawal` deletes the request and decrements the live counter** [2](#0-1) 

`delete withdrawalRequests[requestId]` zeroes `expectedAssetAmount`. The live counter `unlockedWithdrawalsCount[asset]` is decremented. Neither action touches `unlockedCount` in the initializer.

**`finalizeInitialize2` blindly passes the stale accumulated count to `initialize2`** [3](#0-2) 

**`initialize2` overwrites the live counter with the stale value** [4](#0-3) 

**`sweepRemainingAssets` is gated on `hasUnlockedWithdrawals`** [5](#0-4) 

`hasUnlockedWithdrawals` is a pure counter check: [6](#0-5) 

---

### Impact Explanation

After `initialize2` sets `unlockedWithdrawalsCount[asset]` to a value 1 higher than the true remaining count, every subsequent `completeWithdrawal` decrements it. Once all real withdrawals are exhausted the counter sits at **1**, `hasUnlockedWithdrawals` returns `true` forever, and `sweepRemainingAssets` always reverts with `PendingWithdrawalsExist`. The remaining protocol balance in the withdrawal manager is permanently frozen. No non-upgrade path exists to correct the counter.

---

### Likelihood Explanation

The initialization window is not atomic. `processChunk` is called by an operator in one or more transactions; `finalizeInitialize2` is called by a manager in a separate transaction. The gap between them can span many blocks. During this window, `completeWithdrawal` is open to any user whose withdrawal delay has passed — a normal, expected user action. No special role, front-running, or key compromise is required. The condition is reachable whenever at least one user completes a withdrawal during the initialization window, which is likely on a live deployment.

The only mitigation available to operators is to pause the contract before starting `processChunk` and unpause after `finalizeInitialize2`, but this is not enforced by the code.

---

### Recommendation

1. **Enforce atomicity or a pause**: require the contract to be paused during the entire `processChunk` → `finalizeInitialize2` sequence, or perform the full count and `initialize2` call in a single transaction.
2. **Recount at finalization time**: instead of accumulating `unlockedCount` across multiple `processChunk` calls, have `finalizeInitialize2` call `getUnlockedWithdrawalsCount` (which re-reads live state) and pass that value to `initialize2`.
3. **Alternatively**, track completions that occur during the window and subtract them from `unlockedCount` before calling `initialize2`.

---

### Proof of Concept

State-sequence (local fork or unit test, unmodified contracts):

```
// Setup: 3 requests unlocked for asset A (nonces 0,1,2), nextLockedNonce=3
// unlockedWithdrawalsCount[A] = 3 (set by prior unlockQueue calls)

// Step 1: operator calls processChunk(A, 3)
//   → unlockedCount[A] = 3, processedIndex[A] = 3

// Step 2: unlockQueue advances nextLockedNonce to 4 (nonce 3 unlocked)
//   → unlockedWithdrawalsCount[A] = 4

// Step 3: operator calls processChunk(A, 1)
//   → unlockedCount[A] = 4, processedIndex[A] = 4

// Step 4: user calls completeWithdrawal(A) for nonce 0
//   → delete withdrawalRequests[keccak(A,0)]  (expectedAssetAmount → 0)
//   → unlockedWithdrawalsCount[A] = 3

// Step 5: manager calls finalizeInitialize2()
//   → initialize2(4, ...) called
//   → unlockedWithdrawalsCount[A] = 4  (overwritten with stale value)

// Now only 3 real withdrawals remain (nonces 1,2,3).
// After users complete all 3: unlockedWithdrawalsCount[A] = 1
// hasUnlockedWithdrawals(A) == true  ← permanently
// sweepRemainingAssets(A) reverts forever

assert(withdrawalManager.unlockedWithdrawalsCount(A) == 1);
assert(withdrawalManager.hasUnlockedWithdrawals(A) == true);
vm.expectRevert("PendingWithdrawalsExist");
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

**File:** contracts/LRTWithdrawalManager.sol (L118-120)
```text
        unlockedWithdrawalsCount[lrtConfig.getLSTToken(LRTConstants.ST_ETH_TOKEN)] = unlockedWithdrawalsCountSTETH;
        unlockedWithdrawalsCount[lrtConfig.getLSTToken(LRTConstants.ETHX_TOKEN)] = unlockedWithdrawalsCountETHx;
        unlockedWithdrawalsCount[LRTConstants.ETH_TOKEN] = unlockedWithdrawalsCountETH;
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
