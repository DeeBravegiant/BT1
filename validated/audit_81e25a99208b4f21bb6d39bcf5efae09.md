### Title
Missing nonce in `AssetWithdrawalFinalized` event prevents off-chain correlation of completed withdrawal requests - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager` assigns a unique per-asset nonce to every withdrawal request at queue time and emits it in `AssetWithdrawalQueued`, but omits that nonce from `AssetWithdrawalFinalized` when the request is completed. A user with multiple pending withdrawal requests for the same asset cannot be tracked correctly by off-chain tools, mirroring the exact event-data-omission pattern identified in the reference report.

### Finding Description
Each withdrawal request is identified by a unique nonce scoped to an asset. At initiation, `_addUserWithdrawalRequest` assigns `nextUnusedNonce_` to the request and emits it:

```solidity
emit AssetWithdrawalQueued(msg.sender, asset, rsETHUnstaked, nextUnusedNonce_);
``` [1](#0-0) 

The nonce is also the indexed field in the event definition:

```solidity
event AssetWithdrawalQueued(
    address indexed withdrawer, address indexed asset, uint256 rsETHUnstaked, uint256 indexed userNonce
);
``` [2](#0-1) 

When `_processWithdrawalCompletion` finalizes the request, it resolves `usersFirstWithdrawalRequestNonce` from the front of the queue but never includes it in the emitted event:

```solidity
uint256 usersFirstWithdrawalRequestNonce = userAssociatedNonces[asset][user].popFront();
...
emit AssetWithdrawalFinalized(user, asset, request.rsETHUnstaked, request.expectedAssetAmount);
``` [3](#0-2) 

The finalization event definition confirms the nonce is absent:

```solidity
event AssetWithdrawalFinalized(
    address indexed withdrawer, address indexed asset, uint256 amountBurned, uint256 amountReceived
);
``` [4](#0-3) 

### Impact Explanation
Off-chain systems (indexers, frontends, notification services, protocol backends) that listen to `AssetWithdrawalFinalized` to track which specific pending request was settled cannot do so. When a user holds two or more pending withdrawal requests for the same asset with identical `rsETHUnstaked` amounts, the finalization event is indistinguishable between them. An off-chain tool may mark the wrong request as completed, leaving the other incorrectly shown as still pending — or vice versa — causing desynchronized state between on-chain reality and off-chain tracking. This maps to **Low: contract fails to deliver promised returns** (accurate off-chain state tracking) without direct fund loss.

### Likelihood Explanation
Any user who calls `initiateWithdrawal` more than once for the same asset before completing the first request creates the ambiguous condition. This is a normal usage pattern (e.g., a user making incremental withdrawals over time). The `completeWithdrawal` function is publicly callable by any user, making the triggering path fully unprivileged. [5](#0-4) 

### Recommendation
**Short term:** Add `usersFirstWithdrawalRequestNonce` to `AssetWithdrawalFinalized` so every finalization event can be unambiguously correlated to its originating `AssetWithdrawalQueued` event. Update the event definition in `ILRTWithdrawalManager.sol` accordingly.

**Long term:** Audit all other events in the withdrawal lifecycle (`AssetUnlocked`, `AssetWithdrawalCompletedBy`) to ensure they also carry sufficient identifiers for off-chain reconstruction of per-request state.

### Proof of Concept
1. Alice calls `initiateWithdrawal(ETH, 1 ether rsETH, ...)` → nonce 5 assigned, `AssetWithdrawalQueued(..., 5)` emitted.
2. Alice calls `initiateWithdrawal(ETH, 1 ether rsETH, ...)` → nonce 6 assigned, `AssetWithdrawalQueued(..., 6)` emitted.
3. Operator calls `unlockQueue` — both nonces 5 and 6 are unlocked.
4. Alice calls `completeWithdrawal(ETH, ...)` → nonce 5 is popped from the front and settled.
5. Event emitted: `AssetWithdrawalFinalized(Alice, ETH, 1 ether, <amount>)`.
6. An off-chain indexer sees two identical `AssetWithdrawalQueued` events (same amounts) and one `AssetWithdrawalFinalized` event with no nonce. It cannot determine whether nonce 5 or nonce 6 was completed, and may mark nonce 6 as settled while nonce 5 remains incorrectly shown as pending — or the reverse — producing a permanently desynced view of Alice's withdrawal queue. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L183-185)
```text
    function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
        _processWithdrawalCompletion(asset, msg.sender, referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L699-738)
```text
    function _processWithdrawalCompletion(address asset, address user, string calldata referralId) internal {
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

        unlockedWithdrawalsCount[asset]--;

        // If Aave integration is enabled and asset is ETH, withdraw from Aave if needed
        if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN) {
            uint256 contractBalance = address(this).balance;
            if (contractBalance < request.expectedAssetAmount) {
                uint256 amountNeeded = request.expectedAssetAmount - contractBalance;
                _withdrawFromAave(amountNeeded);

                // Verify we have sufficient balance after withdrawal
                uint256 balanceAfter = address(this).balance;
                if (balanceAfter < request.expectedAssetAmount) {
                    revert InsufficientLiquidityForWithdrawal();
                }
            }
        }

        _transferAsset(asset, user, request.expectedAssetAmount);

        emit ReferralIdEmitted(referralId);
        emit AssetWithdrawalFinalized(user, asset, request.rsETHUnstaked, request.expectedAssetAmount);
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

**File:** contracts/interfaces/ILRTWithdrawalManager.sol (L46-48)
```text
    event AssetWithdrawalQueued(
        address indexed withdrawer, address indexed asset, uint256 rsETHUnstaked, uint256 indexed userNonce
    );
```

**File:** contracts/interfaces/ILRTWithdrawalManager.sol (L50-52)
```text
    event AssetWithdrawalFinalized(
        address indexed withdrawer, address indexed asset, uint256 amountBurned, uint256 amountReceived
    );
```
