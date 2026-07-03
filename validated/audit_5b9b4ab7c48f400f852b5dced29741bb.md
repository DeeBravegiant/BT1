### Title
Unbounded Withdrawal Queue Growth via Dust Requests Causes Temporary Freezing of Legitimate User Withdrawals - (File: contracts/LRTWithdrawalManager.sol)

---

### Summary

An unprivileged rsETH holder can call `initiateWithdrawal` with dust amounts (as little as 1 wei of rsETH when `minRsEthAmountToWithdraw` is at its default value of 0) to flood the sequential withdrawal queue. Because `_unlockWithdrawalRequests` must advance `nextLockedNonce` strictly in order, an attacker who occupies low nonces with many dust requests forces the operator to process all of them before any legitimate user request at a higher nonce can be unlocked, temporarily freezing those users' funds.

---

### Finding Description

`initiateWithdrawal` in `LRTWithdrawalManager.sol` is callable by any rsETH holder with no floor on request size when `minRsEthAmountToWithdraw[asset]` has not been explicitly set (it defaults to `0` in Solidity):

```solidity
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}
``` [1](#0-0) 

Each call pushes a new nonce into the global sequential counter and writes a new entry into `withdrawalRequests`:

```solidity
userAssociatedNonces[asset][msg.sender].pushBack(nextUnusedNonce_);
nextUnusedNonce[asset] = nextUnusedNonce_ + 1;
``` [2](#0-1) 

The operator-facing `unlockQueue` → `_unlockWithdrawalRequests` loop advances `nextLockedNonce` **strictly in order**; it cannot skip a nonce:

```solidity
while (nextLockedNonce_ < firstExcludedIndex) {
    ...
    unchecked { nextLockedNonce_++; }
}
nextLockedNonce[asset] = nextLockedNonce_;
``` [3](#0-2) 

A legitimate user whose request lands at nonce `N` cannot have their withdrawal unlocked until every request at nonces `0 … N-1` has been processed. An attacker who pre-fills nonces `0 … N-1` with dust requests therefore blocks the legitimate user for as long as it takes the operator to drain the queue.

The `assetsCommitted` guard does not prevent this: for a 1-wei rsETH request, `getExpectedAssetAmount` returns 1 wei of asset, so `assetsCommitted` grows by only 1 wei per request:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
``` [4](#0-3) 

With a protocol holding, say, 1 000 ETH in assets, an attacker can create up to `1 000 × 10^18` dust requests before the available-asset ceiling is hit — far more than enough to saturate the queue for any practical time horizon.

After the withdrawal delay the attacker recovers their rsETH as assets, making the attack economically self-funding (net cost: gas only).

---

### Impact Explanation

Legitimate users who submit withdrawal requests after the attacker's dust flood cannot have their requests unlocked until the operator has sequentially processed every dust entry. During that period their rsETH is held in the contract and the corresponding asset amount is committed, constituting a **temporary freezing of funds**. The delay is bounded only by how quickly the operator can call `unlockQueue` in batches, which itself incurs unbounded cumulative gas proportional to the number of dust entries.

---

### Likelihood Explanation

- `minRsEthAmountToWithdraw[asset]` defaults to `0`; the protocol must explicitly set it per asset to mitigate this.
- The attacker only needs rsETH (obtainable by depositing ETH/LSTs), which is returned after the withdrawal delay — net cost is gas.
- No special role or privilege is required; `initiateWithdrawal` is open to any address.
- The attack can be executed across many blocks to accumulate a large queue depth before any victim submits their request.

---

### Recommendation

1. **Enforce a meaningful minimum per asset**: Set `minRsEthAmountToWithdraw[asset]` to a non-trivial value (e.g., 0.01 rsETH) for every supported asset at deployment and after each new asset is added.
2. **Per-user request cap**: Introduce a maximum number of pending withdrawal requests per `(asset, user)` pair to prevent a single address from monopolising queue nonces.
3. **Operator skip mechanism**: Allow `unlockQueue` to skip a request (e.g., if its `expectedAssetAmount` is below a dust threshold) so the queue does not become permanently blocked by uneconomic entries.

---

### Proof of Concept

1. Attacker holds 1 000 wei of rsETH (trivially obtainable).
2. Attacker calls `initiateWithdrawal(ETH_TOKEN, 1, "")` 1 000 times, occupying nonces `0 … 999`.
3. Victim calls `initiateWithdrawal(ETH_TOKEN, 10 ether, "")`, landing at nonce `1000`.
4. Operator calls `unlockQueue(ETH_TOKEN, 1001, ...)`. The loop must iterate through all 1 001 entries; the victim's request at nonce `1000` is only unlocked after all 1 000 dust entries are processed.
5. Attacker calls `completeWithdrawal` 1 000 times after the delay, recovering their assets. Net cost: gas.
6. Repeat from step 2 to continuously delay any new victim requests. [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** contracts/LRTWithdrawalManager.sol (L593-593)
```text
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
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
