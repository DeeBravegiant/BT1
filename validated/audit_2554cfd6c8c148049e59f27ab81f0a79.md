### Title
Unbounded Withdrawal Queue Enables Griefing of `unlockQueue` Gas and Temporary Freezing of Legitimate Withdrawals - (File: `contracts/LRTWithdrawalManager.sol`)

---

### Summary

An unprivileged depositor holding rsETH can flood the global withdrawal queue with many minimum-amount `initiateWithdrawal` requests. Because `_unlockWithdrawalRequests` processes the queue in strict FIFO order and there is no per-user or global cap on pending requests, the attacker's entries must be exhausted before any later legitimate user's request can be unlocked. This causes unbounded gas consumption in `unlockQueue` and temporarily freezes legitimate users' withdrawals.

---

### Finding Description

`initiateWithdrawal` accepts any `rsETHUnstaked >= minRsEthAmountToWithdraw[asset]`. The minimum is stored in a mapping that defaults to **zero** and is only set by an admin call to `setMinRsEthAmountToWithdraw`. [1](#0-0) [2](#0-1) 

Every accepted request is appended to the global nonce counter and to the caller's per-user deque: [3](#0-2) 

There is no cap on how many entries a single address may push. The `DoubleEndedQueue` used for `userAssociatedNonces` only reverts on a uint128 wraparound (capacity ≈ 2¹²⁸), which is effectively unlimited: [4](#0-3) 

The operator-callable `unlockQueue` drives `_unlockWithdrawalRequests`, which iterates the queue in FIFO order from `nextLockedNonce` up to the caller-supplied `firstExcludedIndex`: [5](#0-4) 

The loop **breaks** (not continues) on two conditions:

1. `block.number < request.withdrawalStartBlock + withdrawalDelayBlocks` — the delay has not elapsed.
2. `availableAssetAmount < payoutAmount` — insufficient assets remain.

Neither condition skips an entry; both halt the entire loop. Therefore every attacker-inserted entry at the front of the queue must be fully processed (consuming one storage read + multiple writes per iteration) before any later legitimate request is reached.

---

### Impact Explanation

**Unbounded gas consumption (Medium):** If an attacker submits *N* minimum-amount requests before legitimate users, the operator must iterate through all *N* entries in `_unlockWithdrawalRequests` before reaching legitimate requests. Even with batching via `firstExcludedIndex`, each batch call pays gas proportional to the batch size, and the total work is O(N). With `minRsEthAmountToWithdraw = 0` (the default), *N* is bounded only by the total protocol assets divided by 1 wei, which can be astronomically large.

**Temporary freezing of funds (Medium):** Legitimate users whose requests are queued after the attacker's cannot have their withdrawals unlocked until the operator has processed all preceding attacker entries. With the default 8-day withdrawal delay, the attacker can continuously re-submit new requests after each batch is cleared, sustaining the delay indefinitely at low cost. [6](#0-5) 

---

### Likelihood Explanation

- `minRsEthAmountToWithdraw` defaults to **zero** and requires an explicit admin action to set. Many deployments may leave it unset.
- Even when set to a small value (e.g., 0.001 ETH), an attacker holding 1 ETH of rsETH can submit 1 000 requests. The rsETH is returned after the 8-day delay, so the only cost is gas and opportunity cost.
- The attack is permissionless: any rsETH holder can call `initiateWithdrawal`. [7](#0-6) 

---

### Recommendation

1. **Enforce a non-zero `minRsEthAmountToWithdraw`** at initialization time (e.g., require it to be set before the asset is usable for withdrawals).
2. **Add a per-user cap** on the number of simultaneously pending withdrawal requests (e.g., `require(userAssociatedNonces[asset][msg.sender].length() < MAX_PENDING_PER_USER)`).
3. Consider a **small protocol fee** on `initiateWithdrawal` that is forfeited if the request is cancelled, analogous to the `minimumBidQuote` mitigation discussed in the SIZE report.

---

### Proof of Concept

```
// Attacker holds minRsEthAmountToWithdraw * N rsETH (or 1 wei each if minimum == 0)
for (uint i = 0; i < N; i++) {
    lrtWithdrawalManager.initiateWithdrawal(asset, minRsEthAmountToWithdraw, "");
}
// Legitimate user submits their request — it is now at position N in the global queue.
lrtWithdrawalManager.initiateWithdrawal(asset, largeAmount, "");  // victim

// Operator must now call unlockQueue ceil(N / batchSize) times before the victim's
// request is ever reached, delaying their withdrawal by days.
```

The attacker's rsETH is held in the contract and returned after the delay; the only cost is gas and the 8-day lock-up. With `minRsEthAmountToWithdraw == 0`, even 1-wei requests are accepted, making the attack essentially free. [8](#0-7) [5](#0-4)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L35-35)
```text
    mapping(address asset => uint256) public minRsEthAmountToWithdraw;
```

**File:** contracts/LRTWithdrawalManager.sol (L94-94)
```text
        withdrawalDelayBlocks = 8 days / 12 seconds;
```

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

**File:** contracts/utils/DoubleEndedQueue.sol (L53-59)
```text
    function pushBack(Uint256Deque storage deque, uint256 value) internal {
        unchecked {
            uint128 backIndex = deque._end;
            if (backIndex + 1 == deque._begin) revert QueueFull();
            deque._data[backIndex] = value;
            deque._end = backIndex + 1;
        }
```
