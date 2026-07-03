### Title
Missing Withdrawal Cancellation Mechanism Permanently Locks User rsETH Until Completion - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager` uses per-asset nonces to track queued withdrawal requests, but provides no function for a user to cancel or invalidate a pending withdrawal. Once `initiateWithdrawal` is called, the user's rsETH is transferred into the contract and locked for the full withdrawal delay (default 8 days / ~57,600 blocks) with no escape path.

### Finding Description
When a user calls `initiateWithdrawal`, rsETH is immediately pulled from the user via `safeTransferFrom` and a `WithdrawalRequest` is stored under a monotonically-incrementing nonce: [1](#0-0) 

The nonce is assigned and the request is enqueued: [2](#0-1) 

The contract exposes no `cancelWithdrawal` or equivalent function. The only way to recover the rsETH value is to wait for the operator to call `unlockQueue` and then call `completeWithdrawal` — both of which are gated on the withdrawal delay having elapsed and the operator having processed the queue. There is no path for the user to invalidate their own queued nonce and reclaim their rsETH. [3](#0-2) [4](#0-3) 

### Impact Explanation
A user who initiates a withdrawal and subsequently needs to reverse the decision (e.g., exchange rate moved against them, they need rsETH liquidity for another protocol action, or they submitted the wrong asset/amount) has no recourse. Their rsETH is frozen in the contract for at minimum the `withdrawalDelayBlocks` period (default 8 days), and potentially longer if the operator does not call `unlockQueue` promptly. This constitutes **temporary freezing of funds** (Medium severity).

### Likelihood Explanation
Any user who calls `initiateWithdrawal` and later changes their mind faces this issue. Given that rsETH is a yield-bearing LRT token used across DeFi, users frequently need to re-deploy capital quickly. The 8-day default delay makes the freeze material. No special conditions or attacker are required — the user's own valid transaction triggers the lock.

### Recommendation
Add a `cancelWithdrawal(address asset)` function that:
1. Pops the user's oldest (or a specified) nonce from `userAssociatedNonces[asset][msg.sender]`.
2. Verifies the request has **not** yet been unlocked (i.e., its nonce is `>= nextLockedNonce[asset]`).
3. Deletes the `withdrawalRequests[requestId]` entry and decrements `assetsCommitted[asset]`.
4. Returns the locked rsETH to the user via `safeTransfer`.

Only unlocked (operator-processed) requests should be non-cancellable, mirroring the PayrollManager fix pattern of allowing invalidation before execution is finalised.

### Proof of Concept
1. User calls `initiateWithdrawal(stETH, 1e18, "")`. rsETH is transferred to the contract; nonce `N` is assigned.
2. User realises the rsETH/stETH rate has dropped and wants to cancel.
3. User searches the ABI — no `cancelWithdrawal` exists.
4. User must wait ≥ 8 days for `withdrawalDelayBlocks` to pass **and** for the operator to call `unlockQueue`.
5. During this entire window the user's rsETH is frozen and cannot be redeployed, sold, or used as collateral elsewhere. [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L38-50)
```text
    // Next available nonce for withdrawal requests per asset, indicating total requests made.
    mapping(address asset => uint256 nonce) public nextUnusedNonce;

    // Next nonce for which a withdrawal request remains locked.
    mapping(address asset => uint256 requestNonce) public nextLockedNonce;

    // Mapping from a unique request identifier to its corresponding withdrawal request
    mapping(bytes32 requestId => WithdrawalRequest) public withdrawalRequests;

    // Maps each asset to user addresses, pointing to an ordered list of their withdrawal request nonces.
    // Utilizes a double-ended queue for efficient management and removal of initial requests.
    mapping(address asset => mapping(address user => DoubleEndedQueue.Uint256Deque requestNonces)) public
        userAssociatedNonces;
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

**File:** contracts/LRTWithdrawalManager.sol (L183-185)
```text
    function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
        _processWithdrawalCompletion(asset, msg.sender, referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L706-715)
```text
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
