### Title
Absence of Default Minimum Withdrawal Amount Enables Withdrawal Queue Spam, Causing Unbounded Gas in `_unlockWithdrawalRequests` - (File: `contracts/LRTWithdrawalManager.sol`)

---

### Summary

`minRsEthAmountToWithdraw[asset]` defaults to `0` for every asset. The guard in `initiateWithdrawal` reduces to only rejecting a zero-amount call, so any amount ≥ 1 wei of rsETH is accepted. An attacker holding rsETH can create arbitrarily many dust withdrawal requests, bloating the global nonce queue and making the `while` loop inside `_unlockWithdrawalRequests` consume unbounded gas, temporarily preventing legitimate withdrawals from being processed.

---

### Finding Description

`LRTWithdrawalManager` stores a configurable minimum per asset:

```solidity
mapping(address asset => uint256) public minRsEthAmountToWithdraw;
``` [1](#0-0) 

Because Solidity mappings default to `0`, the guard in `initiateWithdrawal` is:

```solidity
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}
``` [2](#0-1) 

When `minRsEthAmountToWithdraw[asset] == 0`, the sub-expression `rsETHUnstaked < 0` is always `false` for `uint256`, so the only effective check is `rsETHUnstaked == 0`. Any call with `rsETHUnstaked = 1` passes.

Each accepted call pushes a new entry into the global nonce sequence via `_addUserWithdrawalRequest`:

```solidity
userAssociatedNonces[asset][msg.sender].pushBack(nextUnusedNonce_);
nextUnusedNonce[asset] = nextUnusedNonce_ + 1;
``` [3](#0-2) 

The operator-facing `unlockQueue` must iterate through every entry between `nextLockedNonce` and `firstExcludedIndex` inside `_unlockWithdrawalRequests`:

```solidity
while (nextLockedNonce_ < firstExcludedIndex) {
    bytes32 requestId = getRequestId(asset, nextLockedNonce_);
    WithdrawalRequest storage request = withdrawalRequests[requestId];
    ...
    unchecked { nextLockedNonce_++; }
}
``` [4](#0-3) 

An attacker who pre-fills the queue with N dust requests forces the operator to iterate through all N entries (each a storage read + write) before reaching any legitimate user's request. With enough entries the loop exceeds the block gas limit even for a small `firstExcludedIndex` window, stalling the unlock pipeline.

The setter exists but is admin-only and is never called during `initialize`, leaving the default at `0`:

```solidity
function setMinRsEthAmountToWithdraw(address asset, uint256 minRsEthAmountToWithdraw_) external onlyLRTAdmin {
    minRsEthAmountToWithdraw[asset] = minRsEthAmountToWithdraw_;
``` [5](#0-4) 

---

### Impact Explanation

**Medium — Unbounded gas consumption / temporary freezing of withdrawal processing.**

The `_unlockWithdrawalRequests` loop is the only path through which queued withdrawal requests are unlocked and made claimable. If the loop cannot complete within a block gas limit for any reasonable `firstExcludedIndex`, legitimate users' requests that sit behind the spam entries cannot be unlocked, temporarily freezing their ability to complete withdrawals. The attacker recovers their rsETH after the delay, so the cost is only gas and opportunity cost.

---

### Likelihood Explanation

**Medium.** The attacker must hold rsETH and pay L1 gas per spam call, but the rsETH is returned after the withdrawal delay. On a chain where rsETH is liquid and gas is cheap (or the attacker is patient), the attack is economically viable. No privileged access is required; `initiateWithdrawal` is a public user-facing function.

---

### Recommendation

Set a non-zero `minRsEthAmountToWithdraw` for every supported asset inside `initialize` (or enforce a non-zero lower bound in `setMinRsEthAmountToWithdraw`). This mirrors the fix applied in the referenced Elys Network remediation: introduce a meaningful economic floor that makes spam economically irrational.

```solidity
function initialize(address lrtConfigAddr) external initializer {
    ...
    // Example: 0.001 rsETH minimum
    minRsEthAmountToWithdraw[ETH_TOKEN] = 1e15;
}
```

Additionally, enforce in `setMinRsEthAmountToWithdraw` that the value cannot be set to `0`:

```solidity
function setMinRsEthAmountToWithdraw(address asset, uint256 min) external onlyLRTAdmin {
    if (min == 0) revert MinimumMustBeNonZero();
    minRsEthAmountToWithdraw[asset] = min;
}
```

---

### Proof of Concept

```solidity
// Attacker holds N * 1 wei rsETH (or splits a larger balance)
// Approve LRTWithdrawalManager to spend rsETH
rsETH.approve(address(withdrawalManager), type(uint256).max);

// Spam N dust withdrawal requests
for (uint256 i = 0; i < N; i++) {
    withdrawalManager.initiateWithdrawal(asset, 1, "spam");
}

// Now the operator's unlockQueue call must iterate through N entries
// before reaching any legitimate user's request.
// With N large enough, the while-loop in _unlockWithdrawalRequests
// exceeds the block gas limit, stalling the entire unlock pipeline.
withdrawalManager.unlockQueue(
    asset,
    nextUnusedNonce,   // firstExcludedIndex = all entries
    minAssetPrice,
    minRsEthPrice,
    maxAssetPrice,
    maxRsEthPrice
); // reverts: out of gas
```

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

**File:** contracts/LRTWithdrawalManager.sol (L330-332)
```text
    function setMinRsEthAmountToWithdraw(address asset, uint256 minRsEthAmountToWithdraw_) external onlyLRTAdmin {
        minRsEthAmountToWithdraw[asset] = minRsEthAmountToWithdraw_;
        emit MinAmountToWithdrawUpdated(asset, minRsEthAmountToWithdraw_);
```

**File:** contracts/LRTWithdrawalManager.sol (L756-757)
```text
        userAssociatedNonces[asset][msg.sender].pushBack(nextUnusedNonce_);
        nextUnusedNonce[asset] = nextUnusedNonce_ + 1;
```

**File:** contracts/LRTWithdrawalManager.sol (L790-814)
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
```
