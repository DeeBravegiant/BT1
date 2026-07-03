Audit Report

## Title
Unbounded Withdrawal Queue Flooding via Dust `initiateWithdrawal` Calls Causes Unbounded Gas Consumption and Temporary Freezing of Funds - (File: `contracts/LRTWithdrawalManager.sol`)

## Summary

`LRTWithdrawalManager.initiateWithdrawal` accepts any non-zero rsETH amount when `minRsEthAmountToWithdraw[asset]` is at its default value of zero, allowing any unprivileged user to flood the global per-asset withdrawal queue with arbitrarily many dust entries. Because `_unlockWithdrawalRequests` iterates the queue in strict FIFO order starting from `nextLockedNonce[asset]`, operators cannot skip over unprocessed dust entries. A sufficiently large queue of dust entries causes unbounded gas consumption per `unlockQueue` call and temporarily freezes legitimate withdrawal requests queued after the dust entries until all preceding entries are cleared across many operator transactions.

## Finding Description

`minRsEthAmountToWithdraw` is a `mapping(address => uint256)` whose Solidity default value is `0` for every asset. [1](#0-0) 

The guard in `initiateWithdrawal` is:

```solidity
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}
``` [2](#0-1) 

When `minRsEthAmountToWithdraw[asset] == 0`, the condition `rsETHUnstaked < 0` is vacuously false for `uint256`, so any amount `> 0` passes. Every successful call pushes a new entry into the global queue and increments `nextUnusedNonce[asset]` without bound: [3](#0-2) 

The operator-facing `unlockQueue` → `_unlockWithdrawalRequests` iterates from `nextLockedNonce[asset]` up to the caller-supplied `firstExcludedIndex` in a `while` loop: [4](#0-3) 

Critically, `nextLockedNonce[asset]` advances only sequentially — the operator controls the upper bound (`firstExcludedIndex`) but cannot set a start offset. Every dust entry must be individually loaded from storage (`SLOAD` for `withdrawalRequests[requestId]`), evaluated, and its `assetsCommitted` accounting updated (`SSTORE`) before the loop can advance past it. The operator must clear all N dust entries across repeated `unlockQueue` calls before any legitimate request queued after them can be unlocked.

The entry point for obtaining rsETH is equally permissive: `minAmountToDeposit` in `LRTDepositPool` also defaults to `0`, so depositing 1 wei of ETH is sufficient to receive a non-zero rsETH balance. [5](#0-4) 

## Impact Explanation

**Medium — Unbounded gas consumption; temporary freezing of legitimate withdrawal funds.**

Each dust entry requires at minimum one `SLOAD` for `withdrawalRequests[requestId]`, one `SSTORE` for `assetsCommitted[asset]`, one `SSTORE` for `request.expectedAssetAmount`, and one `SSTORE` for `unlockedWithdrawalsCount[asset]`. With thousands of dust entries ahead of legitimate requests, the gas cost of each `unlockQueue` call grows proportionally. If the queue depth exceeds what can be processed within the Ethereum block gas limit (~30M gas) in a single call, the operator must issue many sequential transactions to drain the dust before reaching legitimate entries. During this period, legitimate users' rsETH is locked in the contract and their withdrawal requests cannot be unlocked — constituting temporary freezing of funds. Both "Unbounded gas consumption" (Medium) and "Temporary freezing of funds" (Medium) are explicitly in scope.

## Likelihood Explanation

**Medium.** The attacker must hold rsETH for the duration of each pending request (rsETH is transferred into the contract on `initiateWithdrawal`), but it is returned as the underlying asset when eventually processed. The attacker's net cost is only gas. With both `minAmountToDeposit == 0` and `minRsEthAmountToWithdraw == 0` at their defaults, no protocol-level barrier prevents this. The attack is repeatable across multiple addresses and assets, and no privileged access is required — any external caller can trigger it.

## Recommendation

1. **Enforce a non-zero minimum withdrawal amount per asset at initialization.** Set `minRsEthAmountToWithdraw[asset]` to a meaningful floor (e.g., `0.001 ether` worth of rsETH) for every supported asset when the asset is added via `setMinRsEthAmountToWithdraw`, rather than relying on a post-deployment admin call.
2. **Enforce a non-zero minimum deposit amount.** Set `minAmountToDeposit` to a meaningful floor at initialization to raise the cost of obtaining dust rsETH.
3. **Cap the number of pending withdrawal requests per user per asset.** Analogous to `KernelDepositPool.maxNumberOfWithdrawalsPerUser`, add a per-user cap on the `userAssociatedNonces` deque length in `_addUserWithdrawalRequest` to limit how many requests a single address can queue simultaneously.

## Proof of Concept

```solidity
// Preconditions (defaults, no admin action needed):
//   minRsEthAmountToWithdraw[ETH_TOKEN] == 0
//   minAmountToDeposit == 0

// Step 1: Attacker deposits 1 wei ETH → receives tiny rsETH
lrtDepositPool.depositETH{value: 1}(0, "");

// Step 2: Attacker approves withdrawal manager
rsETH.approve(address(withdrawalManager), type(uint256).max);

// Step 3: Repeat N times across multiple attacker addresses — each call costs only gas
for (uint i = 0; i < N; i++) {
    withdrawalManager.initiateWithdrawal(ETH_TOKEN, 1, "spam");
}

// Result: nextUnusedNonce[ETH_TOKEN] == N
// Operator's unlockQueue must now iterate through all N dust entries
// (starting from nextLockedNonce[asset], which cannot be skipped)
// before reaching any legitimate withdrawal request queued after the spam.
// Gas cost of each unlockQueue call scales as O(batch_size),
// and O(N / batch_size) operator calls are required to clear the queue.
// Legitimate users' rsETH remains locked in the contract until all dust is cleared.
```

**Foundry test plan:** Deploy `LRTWithdrawalManager` on a local fork with default configuration. Call `initiateWithdrawal` 10,000 times with 1-wei rsETH amounts from a single EOA. Then call `unlockQueue` with `firstExcludedIndex = nextUnusedNonce` and measure gas. Confirm gas scales linearly with queue depth and that a legitimate request inserted after the dust cannot be unlocked until all preceding dust entries are processed.

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

**File:** contracts/LRTWithdrawalManager.sol (L744-757)
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

**File:** contracts/LRTDepositPool.sol (L657-659)
```text
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }
```
