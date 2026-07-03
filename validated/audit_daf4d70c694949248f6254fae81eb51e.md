The vulnerability is real and traceable. Let me confirm the exact code path before writing the finding.

The two-check design is the root cause:

**Check 1 — `_unlockWithdrawalRequests` line 795** (called from `unlockQueue`): [1](#0-0) 
Uses `withdrawalDelayBlocks` at unlock time. If it passes, `nextLockedNonce[asset]` is advanced past the request.

**Check 2 — `_processWithdrawalCompletion` line 715** (called from `completeWithdrawal`): [2](#0-1) 
Re-reads the **current** `withdrawalDelayBlocks` — not the value at unlock time.

**`setWithdrawalDelayBlocks`** has no snapshot/grandfathering logic: [3](#0-2) 

The gap between the two checks is exploitable by a legitimate (non-malicious) manager action.

---

### Title
Retroactive `withdrawalDelayBlocks` increase freezes already-unlocked withdrawal requests — (`contracts/LRTWithdrawalManager.sol`)

### Summary
`_processWithdrawalCompletion` re-evaluates the withdrawal delay using the **current** `withdrawalDelayBlocks` value at completion time. Because `unlockQueue` already advanced `nextLockedNonce` past a request (proving the old delay had elapsed), any subsequent increase to `withdrawalDelayBlocks` by the LRT manager causes `completeWithdrawal` to revert with `WithdrawalDelayNotPassed` for those already-unlocked requests, temporarily freezing user funds.

### Finding Description

The withdrawal lifecycle has two independent delay checks that both read the global `withdrawalDelayBlocks` storage variable:

1. **`_unlockWithdrawalRequests` (line 795)** — called inside `unlockQueue`. When `block.number >= request.withdrawalStartBlock + withdrawalDelayBlocks`, the request is considered mature: `nextLockedNonce[asset]` is incremented past it and `unlockedWithdrawalsCount[asset]` is bumped. The rsETH is burned and assets are redeemed from the vault at this point. [4](#0-3) 

2. **`_processWithdrawalCompletion` (line 715)** — called inside `completeWithdrawal`. It first confirms the request is unlocked (`nonce < nextLockedNonce`, line 707), then **independently re-checks** the delay: [5](#0-4) 

Both checks read the same mutable `withdrawalDelayBlocks` variable. There is no snapshot of the delay value stored per-request or per-unlock event.

`setWithdrawalDelayBlocks` allows the LRT manager to raise the delay up to `16 days / 12 seconds` (115,200 blocks) from the default of `8 days / 12 seconds` (57,600 blocks): [3](#0-2) 

**Attack sequence:**

| Step | Block | Action |
|------|-------|--------|
| 1 | B | User calls `initiateWithdrawal`; `withdrawalStartBlock = B` |
| 2 | B + 57,600 | Operator calls `unlockQueue`; delay check passes (57,600 ≥ 57,600); `nextLockedNonce` advances past the request |
| 3 | B + 57,601 | LRT manager calls `setWithdrawalDelayBlocks(115_200)` |
| 4 | B + 57,602 | User calls `completeWithdrawal`; line 707 passes (nonce < nextLockedNonce); line 715 **reverts** because `57,602 < B + 115,200` |

The user's funds are locked in the contract until block `B + 115,200`, an additional ~8 days beyond when they were already unlocked. The rsETH has already been burned (step 2), so the user cannot cancel.

### Impact Explanation

**Temporary freezing of funds (Medium).** Users whose requests were already unlocked (rsETH burned, assets redeemed from vault) cannot complete their withdrawal for up to 8 additional days. The freeze is bounded by the maximum delay cap (`16 days / 12 seconds`) minus the original delay (`8 days / 12 seconds`). Funds are not permanently lost but are inaccessible for the freeze window.

### Likelihood Explanation

**Low-Medium.** The LRT manager role is trusted and the action is a legitimate administrative operation (e.g., increasing the delay in response to a security concern). The manager may not be aware that already-unlocked requests will be retroactively re-frozen. The precondition — unlocked-but-not-yet-completed requests existing at the moment of the delay increase — is a normal operational state given the asynchronous nature of the two-step withdrawal process.

### Recommendation

Store the effective delay at the time of unlock (or at request creation) and use that snapshot in `_processWithdrawalCompletion` instead of the current global value. One approach: add a `uint256 effectiveDelayBlocks` field to `WithdrawalRequest` and populate it in `_addUserWithdrawalRequest`. The completion check then becomes:

```solidity
if (block.number < request.withdrawalStartBlock + request.effectiveDelayBlocks)
    revert WithdrawalDelayNotPassed();
```

Alternatively, remove the redundant delay check from `_processWithdrawalCompletion` entirely, since `nextLockedNonce` already guarantees the delay was satisfied at unlock time.

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Pseudocode unit test (Foundry-style)
function test_delayIncreaseFreezesUnlockedWithdrawal() public {
    // 1. User initiates withdrawal at block 0
    vm.roll(0);
    vm.prank(user);
    withdrawalManager.initiateWithdrawal(asset, rsETHAmount, "");

    // 2. Advance past original 8-day delay (57,600 blocks)
    vm.roll(57_600);

    // 3. Operator unlocks the queue — nextLockedNonce advances past user's nonce
    vm.prank(operator);
    withdrawalManager.unlockQueue(asset, 1, minAssetPrice, minRsEthPrice, maxAssetPrice, maxRsEthPrice);

    // Confirm request is unlocked
    assertEq(withdrawalManager.nextLockedNonce(asset), 1);

    // 4. Manager increases delay to 16 days (115,200 blocks)
    vm.prank(manager);
    withdrawalManager.setWithdrawalDelayBlocks(115_200);

    // 5. User tries to complete withdrawal — reverts despite being unlocked
    vm.prank(user);
    vm.expectRevert(ILRTWithdrawalManager.WithdrawalDelayNotPassed.selector);
    withdrawalManager.completeWithdrawal(asset, "");

    // 6. User must wait until block 115,200 — 8 extra days of freeze
    vm.roll(115_200);
    vm.prank(user);
    withdrawalManager.completeWithdrawal(asset, ""); // succeeds only now
}
```

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L338-343)
```text
    function setWithdrawalDelayBlocks(uint256 withdrawalDelayBlocks_) external onlyLRTManager {
        // Set an upper limit of no more than 16 days
        if (withdrawalDelayBlocks_ > 16 days / 12 seconds) revert ExceedWithdrawalDelay();

        withdrawalDelayBlocks = withdrawalDelayBlocks_;
        emit WithdrawalDelayBlocksUpdated(withdrawalDelayBlocks);
```

**File:** contracts/LRTWithdrawalManager.sol (L707-715)
```text
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();

        bytes32 requestId = getRequestId(asset, usersFirstWithdrawalRequestNonce);
        WithdrawalRequest memory request = withdrawalRequests[requestId];

        delete withdrawalRequests[requestId];

        // Check that the withdrawal delay has passed since the request's initiation.
        if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();
```

**File:** contracts/LRTWithdrawalManager.sol (L790-815)
```text
        while (nextLockedNonce_ < firstExcludedIndex) {
            bytes32 requestId = getRequestId(asset, nextLockedNonce_);
            WithdrawalRequest storage request = withdrawalRequests[requestId];

            // Check that the withdrawal delay has passed since the request's initiation.
            if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;

            // Calculate the amount user will receive
            uint256 payoutAmount = _calculatePayoutAmount(request, rsETHPrice, assetPrice);

            if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request

            assetsCommitted[asset] -= request.expectedAssetAmount;
            // Set the amount the user will receive
            request.expectedAssetAmount = payoutAmount;
            rsETHAmountToBurn += request.rsETHUnstaked;
            availableAssetAmount -= payoutAmount;
            assetAmountToUnlock += payoutAmount;

            unlockedWithdrawalsCount[asset]++;

            unchecked {
                nextLockedNonce_++;
            }
        }
        nextLockedNonce[asset] = nextLockedNonce_;
```
