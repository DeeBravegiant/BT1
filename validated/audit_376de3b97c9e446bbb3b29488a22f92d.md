### Title
FIFO Queue Head-Blocking in `_unlockWithdrawalRequests` Temporarily Freezes Subsequent Withdrawals - (File: `contracts/LRTWithdrawalManager.sol`)

---

### Summary

A `break` instead of `continue` at line 800 of `_unlockWithdrawalRequests` causes the entire unlock loop to halt when the head request's `payoutAmount` exceeds the vault's current balance. Because the queue is strictly FIFO and there is no mechanism to skip the head, all subsequent withdrawal requests — regardless of their size — remain locked until the vault accumulates enough assets to satisfy the head request.

---

### Finding Description

`_unlockWithdrawalRequests` iterates from `nextLockedNonce_` up to `firstExcludedIndex`, processing each request in order:

```solidity
// contracts/LRTWithdrawalManager.sol:790-814
while (nextLockedNonce_ < firstExcludedIndex) {
    bytes32 requestId = getRequestId(asset, nextLockedNonce_);
    WithdrawalRequest storage request = withdrawalRequests[requestId];

    if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;

    uint256 payoutAmount = _calculatePayoutAmount(request, rsETHPrice, assetPrice);

    if (availableAssetAmount < payoutAmount) break; // <-- hard stop, not skip

    ...
    unchecked { nextLockedNonce_++; }
}
nextLockedNonce[asset] = nextLockedNonce_;
``` [1](#0-0) 

The `availableAssetAmount` passed into this function is `unstakingVault.balanceOf(asset)` — the vault's live balance at the time `unlockQueue` is called: [2](#0-1) 

`initiateWithdrawal` is fully unprivileged (no role check beyond pause/asset support): [3](#0-2) 

Any user holding sufficient rsETH can queue a large withdrawal request. Once that request sits at the head of the queue (`nextLockedNonce` points to it), every subsequent `unlockQueue` call that finds `vault.balanceOf(asset) < payoutAmount_head` exits the loop immediately at line 800, leaving all requests behind the head permanently locked until the vault balance grows to cover the head.

The operator has no mechanism to skip the head: `firstExcludedIndex` is only an upper bound on the range to process, not a lower bound. The loop always starts from `nextLockedNonce[asset]`. [4](#0-3) 

---

### Impact Explanation

All withdrawal requests queued after the large head request are temporarily frozen. Their rsETH has already been transferred into the contract at `initiateWithdrawal` time (line 166), so users cannot recover their tokens until the head request is eventually unlocked. The freeze persists for as long as the vault balance remains below the head request's `payoutAmount`, which could span multiple unstaking cycles. [5](#0-4) 

**Impact:** Medium — Temporary freezing of funds.

---

### Likelihood Explanation

- No special role is required; any rsETH holder can call `initiateWithdrawal`.
- The vault balance (`unstakingVault.balanceOf(asset)`) fluctuates with the unstaking cadence. A large queued request can easily exceed the vault balance at the time `unlockQueue` is called.
- The attacker does not need to front-run or collude with anyone; they simply need to be first (or early) in the queue with a large amount.
- The check at line 170 (`expectedAssetAmount > getAvailableAssetAmount(asset)`) only prevents queuing more than total protocol assets minus already-committed amounts — it does not prevent queuing more than the vault's current liquid balance. [6](#0-5) 

---

### Recommendation

Replace the `break` at line 800 with a `continue` (or equivalent skip logic) so that requests behind an underfunded head can still be processed when the vault has sufficient balance for them:

```solidity
if (availableAssetAmount < payoutAmount) {
    // Skip this request; try the next one
    unchecked { nextLockedNonce_++; }
    continue;
}
```

Alternatively, maintain a separate "skipped" set and allow the operator to re-attempt skipped requests once the vault is sufficiently funded, preserving FIFO semantics for users while avoiding a full queue stall.

---

### Proof of Concept

```solidity
// Setup:
// - vault.balanceOf(asset) = 100e18
// - Attacker calls initiateWithdrawal(asset, rsETH_large) → nonce 0, payoutAmount = 101e18
// - Victim  calls initiateWithdrawal(asset, rsETH_small) → nonce 1, payoutAmount = 1e18

// Operator calls unlockQueue(asset, 2, ...)
// _unlockWithdrawalRequests receives availableAssetAmount = 100e18

// Iteration 1: nonce=0, payoutAmount=101e18
//   availableAssetAmount(100e18) < payoutAmount(101e18) → BREAK
//   nextLockedNonce stays at 0

// Result: nonce 1 (victim, payoutAmount=1e18) is never reached.
// Victim cannot call completeWithdrawal because:
//   usersFirstWithdrawalRequestNonce(1) >= nextLockedNonce[asset](0) → revert WithdrawalLocked
// Victim's funds are frozen until vault accumulates ≥ 101e18.
``` [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L150-178)
```text
    function initiateWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        override
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }

        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);

        emit ReferralIdEmitted(referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L706-707)
```text
        // Ensure the request is already unlocked.
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
```

**File:** contracts/LRTWithdrawalManager.sol (L782-815)
```text
        if (firstExcludedIndex > nextUnusedNonce[asset]) {
            firstExcludedIndex = nextUnusedNonce[asset];
        }

        uint256 nextLockedNonce_ = nextLockedNonce[asset];
        // Revert when trying to unlock a request that has already been unlocked
        if (nextLockedNonce_ >= firstExcludedIndex) revert NoPendingWithdrawals();

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

**File:** contracts/LRTWithdrawalManager.sol (L846-850)
```text
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
```
