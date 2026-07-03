### Title
Large Withdrawal Request Blocks All Subsequent Queue Entries from Being Unlocked - (File: contracts/LRTWithdrawalManager.sol)

---

### Summary

`LRTWithdrawalManager._unlockWithdrawalRequests` processes withdrawal requests in strict sequential nonce order. When it encounters a request whose required payout exceeds the currently available asset amount, it immediately `break`s, leaving every subsequent request in the queue permanently blocked — even if those later requests require smaller amounts that the available assets could fully cover.

---

### Finding Description

`_unlockWithdrawalRequests` iterates from `nextLockedNonce[asset]` upward:

```solidity
while (nextLockedNonce_ < firstExcludedIndex) {
    ...
    uint256 payoutAmount = _calculatePayoutAmount(request, rsETHPrice, assetPrice);

    if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request
    ...
    nextLockedNonce_++;
}
nextLockedNonce[asset] = nextLockedNonce_;
```

The `break` at line 800 exits the entire loop without advancing `nextLockedNonce`. Because `nextLockedNonce` is the global unlock cursor for the asset, every request with a nonce ≥ the blocked request is also left locked.

`_processWithdrawalCompletion` (called by `completeWithdrawal`) enforces:

```solidity
if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
```

So users whose requests sit behind the blocking entry cannot complete their withdrawals at all — their rsETH is held in the contract with no escape path, because there is no `cancelWithdrawal` function.

**Attack / Scenario path (no attacker required — organic usage suffices):**

1. User A calls `initiateWithdrawal` for a very large rsETH amount → assigned nonce `N`. rsETH is transferred into the contract.
2. Users B, C, D call `initiateWithdrawal` for small amounts → assigned nonces `N+1`, `N+2`, `N+3`.
3. The operator calls `unlockQueue` with `availableAssetAmount` sufficient to cover B, C, D but not A.
4. The loop reaches nonce `N`, computes `payoutAmount > availableAssetAmount`, and `break`s immediately.
5. `nextLockedNonce[asset]` remains at `N`.
6. B, C, D attempt `completeWithdrawal`; all revert with `WithdrawalLocked` because their nonces `N+1`, `N+2`, `N+3` are ≥ `nextLockedNonce`.
7. B, C, D's rsETH remains locked in `LRTWithdrawalManager` indefinitely.

The situation persists until the protocol accumulates enough assets to satisfy A's request in a future `unlockQueue` call. If A's request is very large relative to protocol liquidity, this can be a long-term or permanent freeze.

---

### Impact Explanation

Users whose withdrawal requests are queued behind a single large (or currently-unsatisfiable) request have their rsETH locked in `LRTWithdrawalManager` with no recourse. There is no cancel function, no index-based skip, and no way for the operator to unlock out-of-order. This constitutes **temporary (potentially permanent) freezing of user funds**, matching the "Medium — Temporary freezing of funds" impact tier, and escalating toward Critical if the blocking request can never be satisfied.

---

### Likelihood Explanation

This requires no adversarial action. It occurs naturally whenever:
- A user submits a large withdrawal before others submit smaller ones, and
- The protocol's available unstaked assets are insufficient to cover the large request at the time `unlockQueue` is called.

Both conditions are routine in a liquid restaking protocol where assets are deployed to EigenLayer and only partially available at any given time. Likelihood is **Medium-High**.

---

### Recommendation

Replace the hard `break` with a `continue` (skip the unsatisfiable request and attempt the next one), or introduce a skip-list / index-based unlock so the operator can unlock specific nonces out of order. A minimal fix:

```solidity
// Instead of:
if (availableAssetAmount < payoutAmount) break;

// Use:
if (availableAssetAmount < payoutAmount) {
    unchecked { nextLockedNonce_++; }
    continue;
}
```

Note: skipping a request means its `assetsCommitted` entry must be handled carefully (either kept committed or released). Alternatively, allow users to cancel their own pending (not-yet-unlocked) withdrawal requests and reclaim their rsETH.

---

### Proof of Concept

**Root cause line:** [1](#0-0) 

**The loop that stops advancing `nextLockedNonce` on the first unsatisfiable request:** [2](#0-1) 

**`completeWithdrawal` gate that blocks all users behind the stuck nonce:** [3](#0-2) 

**`initiateWithdrawal` locks rsETH into the contract with no cancel path:** [4](#0-3) 

**Nonces assigned sequentially, so earlier large requests always precede later small ones:** [5](#0-4)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-176)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);

```

**File:** contracts/LRTWithdrawalManager.sol (L705-707)
```text
        uint256 usersFirstWithdrawalRequestNonce = userAssociatedNonces[asset][user].popFront();
        // Ensure the request is already unlocked.
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
```

**File:** contracts/LRTWithdrawalManager.sol (L755-757)
```text
        // Map the user to the newly created request index and increment the nonce for future requests.
        userAssociatedNonces[asset][msg.sender].pushBack(nextUnusedNonce_);
        nextUnusedNonce[asset] = nextUnusedNonce_ + 1;
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
