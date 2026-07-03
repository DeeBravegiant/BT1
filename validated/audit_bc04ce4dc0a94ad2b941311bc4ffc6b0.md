### Title
Unbounded Withdrawal Queue Flooding via Dust `initiateWithdrawal` Calls Causes Unbounded Gas Consumption in `_unlockWithdrawalRequests` - (File: `contracts/LRTWithdrawalManager.sol`)

---

### Summary

`LRTWithdrawalManager.initiateWithdrawal` accepts withdrawal requests with no enforced minimum rsETH amount when `minRsEthAmountToWithdraw[asset]` is at its default value of zero. Any unprivileged user can flood the global per-asset withdrawal queue with arbitrarily many dust requests. The internal `_unlockWithdrawalRequests` function iterates through all pending requests in strict FIFO order; operators cannot skip over unprocessed entries. A sufficiently large queue of dust requests causes unbounded gas consumption in every `unlockQueue` call and can temporarily freeze legitimate withdrawals if the queue grows beyond what a single block can process.

---

### Finding Description

`minRsEthAmountToWithdraw` is a plain `mapping(address => uint256)` whose default value is `0` for every asset. [1](#0-0) 

The guard in `initiateWithdrawal` is:

```solidity
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}
``` [2](#0-1) 

When `minRsEthAmountToWithdraw[asset] == 0` (the default), the condition `rsETHUnstaked < 0` is vacuously false for `uint256`, so any amount `> 0` is accepted. Every successful call pushes a new entry into the global queue and increments `nextUnusedNonce[asset]` without bound: [3](#0-2) 

The operator-facing `unlockQueue` → `_unlockWithdrawalRequests` iterates from `nextLockedNonce[asset]` up to `firstExcludedIndex` in a `while` loop: [4](#0-3) 

Because the queue is strictly FIFO and `nextLockedNonce[asset]` advances only sequentially, the operator cannot skip over unprocessed dust entries. Every dust request must be individually loaded from storage, evaluated, and its `assetsCommitted` accounting updated before the loop can reach a legitimate request queued later.

The entry point for obtaining rsETH is equally permissive: `minAmountToDeposit` in `LRTDepositPool` also defaults to `0`, so depositing 1 wei of ETH is sufficient to receive a non-zero rsETH balance. [5](#0-4) 

---

### Impact Explanation

**Medium — Unbounded gas consumption; temporary freezing of legitimate withdrawal requests.**

Each dust entry in the queue requires at minimum one `SLOAD` for `withdrawalRequests[requestId]`, one `SSTORE` for `assetsCommitted[asset]`, one `SSTORE` for `request.expectedAssetAmount`, and one `SSTORE` for `unlockedWithdrawalsCount[asset]`. With thousands of dust entries ahead of legitimate requests, the gas cost of a single `unlockQueue` call grows proportionally. If the queue depth exceeds what can be processed within the Ethereum block gas limit (~30 M gas), legitimate withdrawal requests queued after the dust entries cannot be unlocked in any single transaction, temporarily freezing those funds until the dust is cleared across many operator calls.

---

### Likelihood Explanation

**Medium.** The attacker must hold rsETH for the duration of each pending request (the rsETH is transferred into the contract on `initiateWithdrawal`). However, the rsETH is not destroyed — it is returned (as the underlying asset) when the request is eventually processed. The attacker's net cost is only gas. With `minAmountToDeposit == 0` and `minRsEthAmountToWithdraw == 0` both at their defaults, no protocol-level barrier prevents this. The attack is repeatable across multiple addresses and assets.

---

### Recommendation

1. **Enforce a non-zero minimum withdrawal amount per asset at initialization.** Set `minRsEthAmountToWithdraw[asset]` to a meaningful floor (e.g., `0.001 ether` worth of rsETH) for every supported asset when the asset is added, rather than relying on a post-deployment admin call.
2. **Enforce a non-zero minimum deposit amount.** Set `minAmountToDeposit` to a meaningful floor at initialization to raise the cost of obtaining dust rsETH.
3. **Cap the number of pending withdrawal requests per user per asset.** Analogous to `KernelDepositPool.maxNumberOfWithdrawalsPerUser`, add a per-user cap on the `userAssociatedNonces` deque length to limit how many requests a single address can queue.

---

### Proof of Concept

```
// Preconditions (defaults, no admin action needed):
//   minRsEthAmountToWithdraw[ETH_TOKEN] == 0
//   minAmountToDeposit == 0

// Step 1: Attacker deposits 1 wei ETH → receives tiny rsETH
lrtDepositPool.depositETH{value: 1}(0, "");

// Step 2: Attacker approves withdrawal manager
rsETH.approve(address(withdrawalManager), type(uint256).max);

// Step 3: Repeat N times — each call costs only gas
for (uint i = 0; i < N; i++) {
    // Split rsETH balance into 1-wei chunks across multiple addresses if needed
    withdrawalManager.initiateWithdrawal(ETH_TOKEN, 1, "spam");
}

// Result: nextUnusedNonce[ETH_TOKEN] == N
// Operator's unlockQueue must now iterate through all N dust entries
// before reaching any legitimate withdrawal request queued after the spam.
// Gas cost of unlockQueue scales as O(N).
``` [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L35-35)
```text
    mapping(address asset => uint256) public minRsEthAmountToWithdraw;
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

**File:** contracts/LRTWithdrawalManager.sol (L770-816)
```text
    function _unlockWithdrawalRequests(
        address asset,
        uint256 availableAssetAmount,
        uint256 rsETHPrice,
        uint256 assetPrice,
        uint256 firstExcludedIndex
    )
        internal
        returns (uint256 rsETHAmountToBurn, uint256 assetAmountToUnlock)
    {
        // Check that upper limit is in the range of existing withdrawal requests. If it is greater set it to the first
        // nonce with no withdrawal request.
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
    }
```

**File:** contracts/LRTDepositPool.sol (L657-659)
```text
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }
```
