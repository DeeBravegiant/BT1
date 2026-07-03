### Title
FIFO Withdrawal Queue Blocking Causes Temporary Freeze of User rsETH - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager._unlockWithdrawalRequests` processes withdrawal requests in strict FIFO order and halts entirely when the first pending request cannot be covered by available assets. Because there is no mechanism to skip a stuck head-of-queue request and no `cancelWithdrawal` function exists, all subsequent users' rsETH is temporarily frozen in the contract with no recovery path until the blocking request is eventually serviced.

### Finding Description
When a user calls `initiateWithdrawal`, their rsETH is transferred into `LRTWithdrawalManager` and a `WithdrawalRequest` is appended to the global per-asset nonce queue. The admission check at line 170 compares against `getAvailableAssetAmount`, which uses `lrtDepositPool.getTotalAssetDeposits(asset)` — the protocol-wide total — not the balance actually sitting in `LRTUnstakingVault`.

```
// LRTWithdrawalManager.sol:168-173
uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
assetsCommitted[asset] += expectedAssetAmount;
```

When the operator later calls `unlockQueue`, the available amount passed to `_unlockWithdrawalRequests` is `unstakingVault.balanceOf(asset)` — only what has been physically returned from EigenLayer. The unlock loop iterates from `nextLockedNonce[asset]` in strict order and **breaks** the moment the head-of-queue request exceeds available assets:

```
// LRTWithdrawalManager.sol:790-814
while (nextLockedNonce_ < firstExcludedIndex) {
    ...
    if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets
    ...
    nextLockedNonce_++;
}
nextLockedNonce[asset] = nextLockedNonce_;
```

`nextLockedNonce[asset]` is only advanced when a request is successfully unlocked. There is no mechanism to skip a request that cannot currently be serviced. Consequently, every request with a nonce ≥ the stuck nonce remains in the `WithdrawalLocked` state indefinitely.

When any of those later users calls `completeWithdrawal`, `_processWithdrawalCompletion` reverts:

```
// LRTWithdrawalManager.sol:705-707
uint256 usersFirstWithdrawalRequestNonce = userAssociatedNonces[asset][user].popFront();
if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
```

There is no `cancelWithdrawal` function anywhere in the contract. The user cannot reclaim their rsETH; it remains locked in the withdrawal manager until the blocking head-of-queue request is eventually unlocked.

**Concrete scenario:**
1. Protocol holds 100 ETH total assets: 80 ETH deployed in EigenLayer, 20 ETH in `LRTUnstakingVault`.
2. User A calls `initiateWithdrawal` for rsETH worth 80 ETH. Admission check passes (`80 < 100`). `assetsCommitted = 80`. Nonce = 0.
3. User B calls `initiateWithdrawal` for rsETH worth 10 ETH. Admission check passes (`10 < 20`). `assetsCommitted = 90`. Nonce = 1.
4. Operator calls `unlockQueue` with 20 ETH available in the vault.
5. `_unlockWithdrawalRequests` evaluates nonce 0 (User A): `20 < 80` → `break`. `nextLockedNonce` stays at 0.
6. User B's nonce 1 is never reached. User B calls `completeWithdrawal` → `WithdrawalLocked` revert.
7. User B has no way to cancel and recover their rsETH.

### Impact Explanation
User B's rsETH is locked inside `LRTWithdrawalManager` with no user-accessible exit path. The funds are not lost permanently — they are released once the operator unstakes enough assets from EigenLayer to cover User A's request and calls `unlockQueue` again — but the freeze duration is unbounded from the user's perspective and entirely outside their control. This constitutes **temporary freezing of funds** (Medium severity per the allowed impact scope).

### Likelihood Explanation
Medium. The gap between `getTotalAssetDeposits` (used for admission) and `unstakingVault.balanceOf` (used for unlocking) is a normal, persistent operational state: the protocol routinely has the majority of assets deployed in EigenLayer strategies. Any user who submits a withdrawal request larger than the current vault balance, followed by one or more smaller requests from other users, triggers this condition. No adversarial intent is required; it arises from ordinary concurrent usage.

### Recommendation
1. **Add a `cancelWithdrawal` function** that allows a user to remove their own pending (still-locked) withdrawal request and receive their rsETH back. This directly mirrors the recommendation in the reference report to dequeue tokens when conditions prevent consumption.
2. **Alternatively**, modify `_unlockWithdrawalRequests` to skip (rather than break on) requests that cannot currently be covered, so that smaller subsequent requests are not blocked by a single large head-of-queue entry.

### Proof of Concept

```
// Pseudocode walkthrough referencing exact lines

// Step 1 – User A queues large withdrawal (nonce 0)
// LRTWithdrawalManager.sol:162-176
initiateWithdrawal(ETH, 80e18_rsETH, "");
// assetsCommitted[ETH] = 80 ETH equivalent
// userAssociatedNonces[ETH][userA] = [0]

// Step 2 – User B queues small withdrawal (nonce 1)
initiateWithdrawal(ETH, 10e18_rsETH, "");
// assetsCommitted[ETH] = 90 ETH equivalent
// userAssociatedNonces[ETH][userB] = [1]

// Step 3 – Operator calls unlockQueue; vault only has 20 ETH
// LRTWithdrawalManager.sol:268-320
unlockQueue(ETH, 2, ...);
// _unlockWithdrawalRequests called with availableAssetAmount = 20 ETH
// Loop iteration nonce=0: payoutAmount=80 ETH > 20 ETH → break (line 800)
// nextLockedNonce[ETH] remains 0

// Step 4 – User B tries to complete withdrawal
// LRTWithdrawalManager.sol:183-184
completeWithdrawal(ETH, "");
// _processWithdrawalCompletion:
//   popFront() → nonce 1
//   1 >= nextLockedNonce[ETH](=0) is FALSE... wait
```

Re-checking the condition: `usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]` → `1 >= 0` is **true**, so it reverts with `WithdrawalLocked`. User B's nonce is popped from their personal queue but the transaction reverts, rolling back the pop. User B is stuck and has no cancel path. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L162-176)
```text
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }

        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);

```

**File:** contracts/LRTWithdrawalManager.sol (L596-603)
```text
    /// @notice Calculates the amount of asset available for withdrawal.
    /// @param asset The asset address.
    /// @return availableAssetAmount The asset amount avaialble for withdrawal.
    function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
        availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L700-715)
```text
        if (userAssociatedNonces[asset][user].empty()) {
            revert NoWithdrawalRequests(user, asset);
        }

        // Retrieve and remove the oldest withdrawal request for the user.
        uint256 usersFirstWithdrawalRequestNonce = userAssociatedNonces[asset][user].popFront();
        // Ensure the request is already unlocked.
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();

        bytes32 requestId = getRequestId(asset, usersFirstWithdrawalRequestNonce);
        WithdrawalRequest memory request = withdrawalRequests[requestId];

        delete withdrawalRequests[requestId];

        // Check that the withdrawal delay has passed since the request's initiation.
        if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();
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
