Looking at the exact code flow across `UnlockedWithdrawalsInitializer.sol` and `LRTWithdrawalManager.sol`:

**`processChunk` (scan-time snapshot):** [1](#0-0) 

**`_processWithdrawalCompletion` (deletes request, decrements live counter):** [2](#0-1) 

**`initialize2` (SETS, does not add-to, the live counter):** [3](#0-2) 

**`sweepRemainingAssets` (permanently blocked if counter > 0):** [4](#0-3) 

**`hasUnlockedWithdrawals`:** [5](#0-4) 

The race condition is real and concrete. Here is the exact sequence:

1. One unlocked request exists (nonce 0, `expectedAssetAmount > 0`). `_unlockWithdrawalRequests` already set `unlockedWithdrawalsCount[asset] = 1`.
2. Operator calls `processChunk` → reads `expectedAssetAmount > 0` → `unlockedCount[asset] = 1`.
3. User calls `completeWithdrawal` → `delete withdrawalRequests[requestId]` (zeroes `expectedAssetAmount`) → `unlockedWithdrawalsCount[asset]-- = 0`. This is correct at this moment.
4. Manager calls `finalizeInitialize2` → calls `initialize2(1, ...)` → **SETS** `unlockedWithdrawalsCount[asset] = 1`, overwriting the correctly-decremented `0`.
5. No real unlocked requests remain. `unlockedWithdrawalsCount[asset]` is permanently stuck at `1`.
6. `hasUnlockedWithdrawals` returns `true` forever. `sweepRemainingAssets` reverts forever.

`completeWithdrawal` has no `onlyBeforeInitialize2` guard — it is callable at any time by any user with an unlocked request. The `reinitializer(2)` on `initialize2` means the overwrite cannot be undone without a new upgrade.

---

### Title
Stale `processChunk` Count Inflates `unlockedWithdrawalsCount` via Race with `completeWithdrawal`, Permanently Freezing Protocol Assets — (`contracts/utils/UnlockedWithdrawalsInitializer.sol`)

### Summary
`processChunk` takes a point-in-time snapshot of unlocked withdrawal counts. If a user calls `completeWithdrawal` between `processChunk` and `finalizeInitialize2`, the request is deleted and the live `unlockedWithdrawalsCount` is correctly decremented. However, `finalizeInitialize2` then calls `initialize2`, which **sets** (not adds to) `unlockedWithdrawalsCount` using the stale snapshot, permanently inflating the counter by one per such race. This causes `hasUnlockedWithdrawals` to return `true` forever, permanently blocking `sweepRemainingAssets`.

### Finding Description
`processChunk` iterates over `withdrawalRequests` and counts entries where `expectedAssetAmount > 0`, accumulating the result in `unlockedCount[asset]`. [1](#0-0) 

`completeWithdrawal` → `_processWithdrawalCompletion` deletes the request and decrements the live `unlockedWithdrawalsCount[asset]`: [2](#0-1) 

`finalizeInitialize2` passes the stale `unlockedCount` to `initialize2`, which **sets** (overwrites) the live counter: [6](#0-5) 

There is no guard on `completeWithdrawal` preventing it from executing between `processChunk` and `finalizeInitialize2`. The `reinitializer(2)` on `initialize2` means the overwrite is permanent. [7](#0-6) 

### Impact Explanation
After the inflated `initialize2` call, `unlockedWithdrawalsCount[asset]` is 1 (or more) higher than the true number of remaining unlocked requests. Once all real requests are claimed, the counter never reaches zero. `hasUnlockedWithdrawals` permanently returns `true`, and `sweepRemainingAssets` permanently reverts at the guard: [8](#0-7) 

Any residual protocol assets (dust, rounding, future deposits) held by the `LRTWithdrawalManager` are permanently frozen with no recovery path in the current code. Impact: **Critical — Permanent freezing of funds**.

### Likelihood Explanation
The window between `processChunk` (operator tx) and `finalizeInitialize2` (manager tx) spans at least one block and typically many blocks in a multi-step upgrade. Any user with an unlocked, delay-passed withdrawal request can trigger this by simply calling `completeWithdrawal` — a normal, permissionless production action. No admin compromise, front-running, or brute force is required.

### Recommendation
Replace the SET semantics in `initialize2` with an ADD-TO approach, or — preferably — take the snapshot in `processChunk` only after pausing `completeWithdrawal` (via the pause gate), or re-read the live `unlockedWithdrawalsCount` directly from the withdrawal manager at `finalizeInitialize2` time instead of relying on the stale `unlockedCount` accumulated by `processChunk`.

### Proof of Concept
```solidity
// 1. One unlocked request exists for ETH (nonce 0, expectedAssetAmount = 1 ether)
//    _unlockWithdrawalRequests already set unlockedWithdrawalsCount[ETH] = 1

// 2. Operator calls processChunk → unlockedCount[ETH] = 1 (snapshot taken)
initializer.processChunk(ETH, 100);

// 3. User calls completeWithdrawal → request deleted, unlockedWithdrawalsCount[ETH]-- = 0
withdrawalManager.completeWithdrawal(ETH, "");

// 4. Manager calls finalizeInitialize2 → initialize2(0, 0, 1) → SETS unlockedWithdrawalsCount[ETH] = 1
initializer.finalizeInitialize2();

// 5. Assert: no real requests remain, but counter is 1
assert(withdrawalManager.unlockedWithdrawalsCount(ETH) == 1);
assert(withdrawalManager.hasUnlockedWithdrawals(ETH) == true);

// 6. sweepRemainingAssets reverts forever
vm.expectRevert(PendingWithdrawalsExist.selector);
withdrawalManager.sweepRemainingAssets(ETH);
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

**File:** contracts/LRTWithdrawalManager.sol (L109-121)
```text
    function initialize2(
        uint256 unlockedWithdrawalsCountETHx,
        uint256 unlockedWithdrawalsCountSTETH,
        uint256 unlockedWithdrawalsCountETH
    )
        external
        reinitializer(2)
        onlyRole(LRTConstants.UNLOCKED_WITHDRAWAL_INITIALIZER)
    {
        unlockedWithdrawalsCount[lrtConfig.getLSTToken(LRTConstants.ST_ETH_TOKEN)] = unlockedWithdrawalsCountSTETH;
        unlockedWithdrawalsCount[lrtConfig.getLSTToken(LRTConstants.ETHX_TOKEN)] = unlockedWithdrawalsCountETHx;
        unlockedWithdrawalsCount[LRTConstants.ETH_TOKEN] = unlockedWithdrawalsCountETH;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L395-413)
```text
    function sweepRemainingAssets(address asset)
        external
        nonReentrant
        onlySupportedAsset(asset)
        onlyLRTManager
        returns (uint256 transferredAmount)
    {
        // Check that all withdrawals are completed
        if (hasUnlockedWithdrawals(asset)) revert PendingWithdrawalsExist();

        uint256 balance = _getAssetBalance(asset);
        if (balance == 0) revert AmountMustBeGreaterThanZero();

        // Transfer to treasury
        address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        _transferAsset(asset, treasury, balance);

        emit RemainingAssetsSwept(asset, balance, treasury);
        return balance;
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
