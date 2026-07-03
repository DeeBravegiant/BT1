### Title
FIFO Queue Head-Blocking: Large Withdrawal Request Temporarily Freezes All Subsequent Requests — (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

`_unlockWithdrawalRequests` uses a hard `break` when the head request's payout exceeds the unstaking vault's current balance. Because the queue is strictly FIFO with no skip mechanism, any single large withdrawal request at the head permanently stalls all smaller requests behind it until the vault accumulates sufficient assets.

---

### Finding Description

`unlockQueue` calls `_unlockWithdrawalRequests`, which iterates from `nextLockedNonce[asset]` upward: [1](#0-0) 

```solidity
while (nextLockedNonce_ < firstExcludedIndex) {
    ...
    uint256 payoutAmount = _calculatePayoutAmount(request, rsETHPrice, assetPrice);

    if (availableAssetAmount < payoutAmount) break; // ← hard break, not continue
```

The `availableAssetAmount` fed into this loop is `unstakingVault.balanceOf(asset)`: [2](#0-1) 

This is **distinct** from the `getAvailableAssetAmount` check used in `initiateWithdrawal`: [3](#0-2) 

`initiateWithdrawal` validates against `totalAssets - assetsCommitted` (deposit pool), while `_unlockWithdrawalRequests` validates against the unstaking vault's live balance. A request can pass the initiation check yet still exceed the vault balance at unlock time.

There is no mechanism to skip the head request. `firstExcludedIndex` is only an upper bound — the loop always starts at `nextLockedNonce_`. The operator cannot call `unlockQueue` with any parameters that would bypass the blocking head entry.

---

### Impact Explanation

All withdrawal requests for the affected asset with nonces `> nextLockedNonce[asset]` are frozen until the unstaking vault accumulates enough balance to cover the head request. Users behind the large request cannot call `completeWithdrawal` because their nonces remain `>= nextLockedNonce[asset]`: [4](#0-3) 

This matches **Medium — Temporary freezing of funds**. The freeze lifts once the vault balance grows to cover the head request, but the duration is unbounded and outside the affected users' control.

---

### Likelihood Explanation

- `initiateWithdrawal` is permissionless for any rsETH holder.
- The unstaking vault balance is operationally variable and routinely lower than total protocol assets.
- No minimum or maximum withdrawal size cap prevents a user from queuing a request sized to the full available deposit pool balance.
- The scenario arises naturally (large legitimate withdrawal queued before vault is replenished) and can be deliberately triggered by a griefing actor.

---

### Recommendation

Replace the `break` with a `continue` (or equivalent skip logic) when a single request's payout exceeds available assets, so the loop can unlock smaller eligible requests behind it. Alternatively, implement a "skip-and-requeue" mechanism for oversized head requests, or enforce a per-request maximum size relative to the unstaking vault's expected replenishment capacity.

---

### Proof of Concept

```solidity
// Setup:
// 1. Attacker calls initiateWithdrawal(asset, LARGE_AMOUNT) → nonce 0, expectedAssetAmount = 100e18
// 2. Victim calls initiateWithdrawal(asset, SMALL_AMOUNT)  → nonce 1, expectedAssetAmount = 1e18
// 3. unstakingVault.balanceOf(asset) = 99e18 (just below large request)

// Operator calls:
unlockQueue(asset, 2, ...);

// Inside _unlockWithdrawalRequests:
//   nextLockedNonce_ = 0 → payoutAmount = 100e18, availableAssetAmount = 99e18
//   → break immediately
//   nextLockedNonce[asset] stays at 0

// Result:
// - Nonce 0 (large): still locked
// - Nonce 1 (small, 1e18 << 99e18 available): also still locked
// - Victim cannot call completeWithdrawal → WithdrawalLocked revert
// - Freeze persists until vault receives ≥ 100e18
```

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L170-170)
```text
        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
```

**File:** contracts/LRTWithdrawalManager.sol (L707-707)
```text
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
```

**File:** contracts/LRTWithdrawalManager.sol (L790-800)
```text
        while (nextLockedNonce_ < firstExcludedIndex) {
            bytes32 requestId = getRequestId(asset, nextLockedNonce_);
            WithdrawalRequest storage request = withdrawalRequests[requestId];

            // Check that the withdrawal delay has passed since the request's initiation.
            if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;

            // Calculate the amount user will receive
            uint256 payoutAmount = _calculatePayoutAmount(request, rsETHPrice, assetPrice);

            if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request
```

**File:** contracts/LRTWithdrawalManager.sol (L846-850)
```text
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
```
