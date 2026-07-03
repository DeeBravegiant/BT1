### Title
Missing Minimum Withdrawal Amount Allows Queue Inflation and Temporary Withdrawal Freeze - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`minRsEthAmountToWithdraw[asset]` defaults to `0` for every asset. An unprivileged rsETH holder can call `initiateWithdrawal` with dust amounts (e.g., 1 wei of rsETH) an arbitrary number of times, inflating the global FIFO withdrawal queue. Because `_unlockWithdrawalRequests` must process all pending requests in strict nonce order before reaching later legitimate requests, legitimate users' withdrawals are temporarily frozen until the operator exhausts the attacker-inflated prefix of the queue.

### Finding Description

`initiateWithdrawal` enforces:

```solidity
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}
``` [1](#0-0) 

`minRsEthAmountToWithdraw` is a plain mapping with no initializer assignment, so it is `0` for every asset until an admin explicitly calls `setMinRsEthAmountToWithdraw`. [2](#0-1) 

When the minimum is `0`, the only guard is `rsETHUnstaked == 0`, meaning any 1-wei amount passes. Each successful call pushes a new nonce to the back of the global per-asset queue and increments `nextUnusedNonce[asset]`:

```solidity
userAssociatedNonces[asset][msg.sender].pushBack(nextUnusedNonce_);
nextUnusedNonce[asset] = nextUnusedNonce_ + 1;
``` [3](#0-2) 

The operator-facing `unlockQueue` → `_unlockWithdrawalRequests` iterates the queue in strict FIFO order from `nextLockedNonce` up to `firstExcludedIndex`:

```solidity
while (nextLockedNonce_ < firstExcludedIndex) {
    ...
    unchecked { nextLockedNonce_++; }
}
nextLockedNonce[asset] = nextLockedNonce_;
``` [4](#0-3) 

There is no mechanism to skip or reorder entries. Every attacker-created dust request occupies a nonce slot that the operator must advance through before any later legitimate request can be unlocked. A user whose request sits behind N attacker requests cannot call `completeWithdrawal` until `nextLockedNonce[asset]` has been advanced past all N slots, because `completeWithdrawal` → `_processWithdrawalCompletion` enforces:

```solidity
if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
``` [5](#0-4) 

### Impact Explanation

Legitimate users whose withdrawal requests are queued after the attacker's dust requests cannot complete their withdrawals until the operator processes every preceding dust entry. With thousands of dust entries, this requires many operator `unlockQueue` calls (each bounded by `firstExcludedIndex`), causing a **temporary freezing of funds** for all users who queued after the attack. This maps directly to the external report's fund-freeze impact class.

### Likelihood Explanation

The attack requires only that the attacker holds a small amount of rsETH (or can acquire it) and pays gas per call. `minRsEthAmountToWithdraw` is `0` by default and must be explicitly set by an admin per asset; any asset for which the admin has not yet called `setMinRsEthAmountToWithdraw` is permanently vulnerable. The entry path (`initiateWithdrawal`) is fully public and permissionless.

### Recommendation

1. Set a non-zero default for `minRsEthAmountToWithdraw` in the `initialize` function for each supported asset, or enforce `minRsEthAmountToWithdraw[asset] > 0` as a precondition before the asset is usable for withdrawals.
2. Consider adding a check that `expectedAssetAmount > 0` after computing it, to reject requests that round down to zero asset payout.

### Proof of Concept

1. Deploy with default state: `minRsEthAmountToWithdraw[ETH_TOKEN] == 0`.
2. Attacker acquires a small amount of rsETH (e.g., 10,000 wei).
3. Attacker calls `initiateWithdrawal(ETH_TOKEN, 1, "")` 10,000 times, creating 10,000 dust entries at nonces `[0, 9999]`.
4. Legitimate user calls `initiateWithdrawal(ETH_TOKEN, 1 ether, "")`, receiving nonce `10000`.
5. Operator calls `unlockQueue` repeatedly; each call advances `nextLockedNonce` through the attacker's dust entries.
6. Until all 10,000 dust entries are processed, the legitimate user's `completeWithdrawal` reverts with `WithdrawalLocked` — their funds are temporarily frozen.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L35-35)
```text
    mapping(address asset => uint256) public minRsEthAmountToWithdraw;
```

**File:** contracts/LRTWithdrawalManager.sol (L162-164)
```text
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }
```

**File:** contracts/LRTWithdrawalManager.sol (L707-707)
```text
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
```

**File:** contracts/LRTWithdrawalManager.sol (L756-757)
```text
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
