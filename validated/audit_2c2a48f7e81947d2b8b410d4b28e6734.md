### Title
Increasing `withdrawalDelayBlocks` retroactively freezes already-unlocked withdrawal requests — (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

`_processWithdrawalCompletion` re-evaluates the current global `withdrawalDelayBlocks` at completion time (line 715), even for requests that were already fully unlocked by `_unlockWithdrawalRequests`. A legitimate call to `setWithdrawalDelayBlocks` with a larger value between the unlock and the completion steps causes `completeWithdrawal` to revert with `WithdrawalDelayNotPassed`, temporarily freezing the user's funds.

---

### Finding Description

**Unlock path** — `_unlockWithdrawalRequests` (called from `unlockQueue`) advances `nextLockedNonce[asset]` past a request only after confirming the delay has elapsed:

```solidity
// line 795
if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;
``` [1](#0-0) 

Once this passes, the request is considered unlocked: `nextLockedNonce[asset]` is incremented, `unlockedWithdrawalsCount[asset]` is incremented, and the asset amount is redeemed from the vault.

**Completion path** — `_processWithdrawalCompletion` first confirms the request is unlocked (line 707), then independently re-checks the delay using the **current** `withdrawalDelayBlocks`:

```solidity
// line 707
if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
// ...
// line 715
if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();
``` [2](#0-1) 

**The gap** — `setWithdrawalDelayBlocks` has no lower-bound guard and no snapshot mechanism; it overwrites the single global variable immediately:

```solidity
function setWithdrawalDelayBlocks(uint256 withdrawalDelayBlocks_) external onlyLRTManager {
    if (withdrawalDelayBlocks_ > 16 days / 12 seconds) revert ExceedWithdrawalDelay();
    withdrawalDelayBlocks = withdrawalDelayBlocks_;
    ...
}
``` [3](#0-2) 

Because the completion check at line 715 reads the **live** `withdrawalDelayBlocks` rather than the value that was in effect when the request was unlocked, any increase to the delay retroactively re-locks already-unlocked requests from the user's perspective.

---

### Impact Explanation

**Temporary freezing of funds (Medium).** The user's withdrawal is stuck until `block.number >= request.withdrawalStartBlock + newDelay`. The maximum additional freeze is bounded by the cap: `(16 days / 12 s) − (8 days / 12 s) = 57,600 blocks ≈ 8 days`. The funds are not lost, but the user cannot access them for the additional period.

The `unlockedWithdrawalsCount` and `nextLockedNonce` are already advanced, so the protocol's internal accounting treats the request as unlocked while the user cannot actually complete it — a state inconsistency.

---

### Likelihood Explanation

**Low-to-medium.** The LRT manager is a trusted role, but increasing `withdrawalDelayBlocks` is a routine operational action (e.g., responding to a security incident or adjusting to a new validator exit timeline). The manager need not be malicious; the freeze is a side-effect of a legitimate parameter update. The window between `unlockQueue` and `completeWithdrawal` can span many blocks in normal operation, making the race condition realistic.

---

### Recommendation

Snapshot `withdrawalDelayBlocks` into the `WithdrawalRequest` struct at the time the request is **unlocked** (inside `_unlockWithdrawalRequests`), and use that stored value in `_processWithdrawalCompletion` instead of the live global. Alternatively, remove the redundant delay check at line 715 entirely — the `nextLockedNonce` guard at line 707 already guarantees the delay was satisfied at unlock time.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Pseudocode unit test (Foundry / Hardhat fork)
function test_freezeAfterDelayIncrease() public {
    // 1. User initiates withdrawal at block N
    vm.roll(N);
    withdrawalManager.initiateWithdrawal(asset, rsETHAmount, "");

    // 2. Advance past original delay (8 days / 12 s = 57,600 blocks)
    vm.roll(N + 57_600);

    // 3. Operator unlocks the queue — succeeds, nextLockedNonce advances
    withdrawalManager.unlockQueue(asset, type(uint256).max, ...);
    // assert: nextLockedNonce[asset] > userNonce  ✓

    // 4. Manager increases delay to 16 days
    vm.prank(lrtManager);
    withdrawalManager.setWithdrawalDelayBlocks(16 days / 12 seconds); // 115_200 blocks

    // 5. User tries to complete — reverts because block.number (N+57600) < N + 115200
    vm.expectRevert(ILRTWithdrawalManager.WithdrawalDelayNotPassed.selector);
    withdrawalManager.completeWithdrawal(asset, "");

    // 6. Advance to new delay — now succeeds
    vm.roll(N + 115_200);
    withdrawalManager.completeWithdrawal(asset, ""); // ✓
}
```

The test exercises unmodified production code. Steps 3–5 demonstrate the invariant break: a request that passed the `nextLockedNonce` guard (line 707) is still blocked by the retroactively enlarged delay check (line 715). [4](#0-3)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L338-344)
```text
    function setWithdrawalDelayBlocks(uint256 withdrawalDelayBlocks_) external onlyLRTManager {
        // Set an upper limit of no more than 16 days
        if (withdrawalDelayBlocks_ > 16 days / 12 seconds) revert ExceedWithdrawalDelay();

        withdrawalDelayBlocks = withdrawalDelayBlocks_;
        emit WithdrawalDelayBlocksUpdated(withdrawalDelayBlocks);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L699-717)
```text
    function _processWithdrawalCompletion(address asset, address user, string calldata referralId) internal {
        if (userAssociatedNonces[asset][user].empty()) {
            revert NoWithdrawalRequests(user, asset);
        }

        // Retrieve and remove the oldest withdrawal request for the user.
        uint256 usersFirstWithdrawalRequestNonce = userAssociatedNonces[asset][user].popFront();
        // Ensure the request is already unlocked.
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();

        bytes32 requestId = getRequestId(asset, usersFirstWithdrawalRequestNonce);
        WithdrawalRequest memory request = withdrawalRequests[requestId];

        delete withdrawalRequests[requestId];

        // Check that the withdrawal delay has passed since the request's initiation.
        if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();

        unlockedWithdrawalsCount[asset]--;
```

**File:** contracts/LRTWithdrawalManager.sol (L790-795)
```text
        while (nextLockedNonce_ < firstExcludedIndex) {
            bytes32 requestId = getRequestId(asset, nextLockedNonce_);
            WithdrawalRequest storage request = withdrawalRequests[requestId];

            // Check that the withdrawal delay has passed since the request's initiation.
            if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;
```
