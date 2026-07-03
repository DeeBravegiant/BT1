Audit Report

## Title
Stale `processChunk` Counts Permanently Over-Seed `unlockedWithdrawalsCount` After `initialize2` — (`contracts/utils/UnlockedWithdrawalsInitializer.sol`)

## Summary

`processChunk` accumulates unlocked-withdrawal counts into `unlockedCount[asset]` and permanently advances `processedIndex[asset]`. If `completeWithdrawal` executes between a `processChunk` call and `finalizeInitialize2`, the already-scanned indices are never re-examined, yet `finalizeInitialize2` passes the stale `unlockedCount` values directly to `initialize2`, which overwrites the live `unlockedWithdrawalsCount` in `LRTWithdrawalManager`. The result is a permanently inflated counter that blocks `sweepRemainingAssets` for the affected asset.

## Finding Description

**Step 1 — `processChunk` accumulates and locks in a count.**

For each index in `[processedIndex[asset], nextLockedNonce[asset])`, the function reads `withdrawalRequests[requestId].expectedAssetAmount`. If non-zero, the request is counted as unlocked. The result is added to `unlockedCount[asset]` and `processedIndex[asset]` is advanced past those indices permanently — they will never be re-scanned. [1](#0-0) 

**Step 2 — `completeWithdrawal` deletes the request data and decrements the live counter.**

`delete withdrawalRequests[requestId]` zeroes `expectedAssetAmount`. Any index already counted by `processChunk` and then completed before `finalizeInitialize2` will have `expectedAssetAmount == 0` on-chain, but `unlockedCount[asset]` in the initializer still reflects the pre-completion value. Critically, `unlockedWithdrawalsCount[asset]--` is also executed, so the live counter is correctly decremented — but this decrement is later overwritten. [2](#0-1) 

**Step 3 — `isAssetComplete` does not detect the staleness.**

`isAssetComplete` only checks `processedIndex[asset] >= nextLockedNonce[asset]`. Completions do not change `nextLockedNonce`, so the check passes even when the accumulated count is stale.

<cite repo="Alyssadaypin/LRT-rsETH--001" path="

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

**File:** contracts/LRTWithdrawalManager.sol (L712-717)
```text
        delete withdrawalRequests[requestId];

        // Check that the withdrawal delay has passed since the request's initiation.
        if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();

        unlockedWithdrawalsCount[asset]--;
```
