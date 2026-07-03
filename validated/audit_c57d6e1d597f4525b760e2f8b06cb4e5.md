### Title
Dust Withdrawal Requests Bloat Queue Causing Unbounded Gas and Temporary Fund Freeze - (File: `contracts/LRTWithdrawalManager.sol`)

---

### Summary

`minRsEthAmountToWithdraw[asset]` defaults to `0` for every asset because it is never set in `initialize`. The guard in `initiateWithdrawal` only blocks a strictly-zero amount; any value ≥ 1 wei passes. An unprivileged attacker holding rsETH can therefore spam the global withdrawal queue with thousands of dust-sized requests. Because `_unlockWithdrawalRequests` iterates the queue sequentially and cannot skip entries, legitimate users' withdrawals are delayed until every spam entry is processed, and each `unlockQueue` call consumes gas proportional to the number of spam entries it must traverse.

---

### Finding Description

**Root cause — unset minimum defaults to zero**

`LRTWithdrawalManager.initialize` never assigns `minRsEthAmountToWithdraw`: [1](#0-0) 

The mapping therefore returns `0` for every asset. The guard in `initiateWithdrawal` is:

```solidity
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}
``` [2](#0-1) 

When `minRsEthAmountToWithdraw[asset] == 0`, the second condition (`rsETHUnstaked < 0`) is always false for a `uint256`, so any `rsETHUnstaked >= 1` passes. A 1-wei withdrawal request is accepted.

**Queue bloat — sequential, non-skippable processing**

Every accepted request is appended to the global sequential queue via `_addUserWithdrawalRequest`, incrementing `nextUnusedNonce[asset]`: [3](#0-2) 

`_unlockWithdrawalRequests` processes entries in strict FIFO order. The operator controls `firstExcludedIndex` to bound a single call, but cannot skip entries — `nextLockedNonce` must advance through every spam entry before reaching legitimate requests: [4](#0-3) 

Each spam entry requires: two storage reads (`getRequestId` + `withdrawalRequests`), arithmetic in `_calculatePayoutAmount`, and two storage writes (`assetsCommitted`, `request.expectedAssetAmount`). With N spam entries ahead of a legitimate request, the operator must make O(N) iterations across one or more `unlockQueue` calls before the legitimate request is unlocked.

---

### Impact Explanation

**Temporary freezing of funds (Medium).** Legitimate users who called `initiateWithdrawal` cannot call `completeWithdrawal` until their nonce is past `nextLockedNonce[asset]`. If an attacker inserts K spam entries before a victim's entry, the victim's withdrawal is delayed by however many `unlockQueue` batches are needed to drain K entries. With a default `withdrawalDelayBlocks` of 8 days and no cap on queue depth, the delay can be extended arbitrarily.

**Unbounded gas consumption (Medium).** Each `unlockQueue` call that traverses spam entries burns gas proportional to the batch size. If the operator tries to process a large batch to catch up, the transaction may exceed the block gas limit, forcing smaller batches and more calls.

---

### Likelihood Explanation

The attacker must hold rsETH and pay gas per request. However:
- The underlying asset is returned when the dust withdrawal completes, so the net cost is gas only.
- On L1 Ethereum, gas costs are non-trivial but a determined attacker can pre-fund thousands of requests in a single block using a contract loop.
- `minRsEthAmountToWithdraw` is not set in `initialize` and there is no on-chain enforcement that the admin must set it before the contract is used.

Likelihood is **Medium**.

---

### Recommendation

1. Set a sensible non-zero default for `minRsEthAmountToWithdraw` inside `initialize` (e.g., `0.001 ether` worth of rsETH).
2. In `setMinRsEthAmountToWithdraw`, enforce `minRsEthAmountToWithdraw_ > 0` so the admin cannot accidentally reset it to zero.
3. Optionally, add a per-user cap on the number of pending withdrawal requests per asset to bound queue growth from a single address.

---

### Proof of Concept

```solidity
// Attacker holds rsETH and calls initiateWithdrawal in a loop
// minRsEthAmountToWithdraw[ETH] == 0 (default), so 1 wei passes the guard

for (uint256 i = 0; i < 10_000; i++) {
    withdrawalManager.initiateWithdrawal(
        LRTConstants.ETH_TOKEN,
        1,          // 1 wei rsETH — passes the zero-check
        ""
    );
}

// Victim calls initiateWithdrawal with a legitimate amount
withdrawalManager.initiateWithdrawal(ETH_TOKEN, 1 ether, "");

// Operator must now call unlockQueue and iterate through 10,000 spam entries
// before nextLockedNonce reaches the victim's entry.
// Each unlockQueue call is bounded by firstExcludedIndex but cannot skip entries.
// Victim's completeWithdrawal reverts with WithdrawalLocked until all spam is drained.
```

The attacker recovers 10,000 wei of ETH when the spam entries are eventually unlocked; the only cost is gas. The victim's 1 ETH withdrawal is frozen until the operator processes all 10,000 spam entries.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L88-98)
```text
    /// @notice Initializes the contract
    /// @param lrtConfigAddr LRT config address
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        __Pausable_init();
        __ReentrancyGuard_init();
        withdrawalDelayBlocks = 8 days / 12 seconds;

        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L162-164)
```text
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }
```

**File:** contracts/LRTWithdrawalManager.sol (L744-759)
```text
    function _addUserWithdrawalRequest(address asset, uint256 rsETHUnstaked, uint256 expectedAssetAmount) internal {
        uint256 nextUnusedNonce_ = nextUnusedNonce[asset];

        // Generate a unique identifier for the new withdrawal request.
        bytes32 requestId = getRequestId(asset, nextUnusedNonce_);

        // Create and store the new withdrawal request.
        withdrawalRequests[requestId] = WithdrawalRequest({
            rsETHUnstaked: rsETHUnstaked, expectedAssetAmount: expectedAssetAmount, withdrawalStartBlock: block.number
        });

        // Map the user to the newly created request index and increment the nonce for future requests.
        userAssociatedNonces[asset][msg.sender].pushBack(nextUnusedNonce_);
        nextUnusedNonce[asset] = nextUnusedNonce_ + 1;

        emit AssetWithdrawalQueued(msg.sender, asset, rsETHUnstaked, nextUnusedNonce_);
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
