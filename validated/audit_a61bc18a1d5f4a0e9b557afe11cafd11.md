### Title
Missing Cancel Mechanism in `initiateWithdrawal` Permanently Locks User rsETH - (File: `contracts/LRTWithdrawalManager.sol`)

---

### Summary

`LRTWithdrawalManager.initiateWithdrawal` transfers rsETH from the user into the contract and queues a withdrawal request, but no cancel/abort function exists for users to reclaim their rsETH if the withdrawal cannot be processed. This is the direct analog of the reference bug: a user-initiated action deposits funds, and the cleanup/cancellation path is entirely absent, leaving those funds irrecoverable.

---

### Finding Description

`initiateWithdrawal` pulls rsETH from the caller and stores a `WithdrawalRequest` in the locked queue:

```solidity
IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
// ...
assetsCommitted[asset] += expectedAssetAmount;
_addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);
```

The rsETH is held in the contract until an operator calls `unlockQueue`, which burns it:

```solidity
if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
```

`unlockQueue` is gated by `onlySupportedAsset(asset)`. If the asset is removed from the supported list after a user has queued a withdrawal, `unlockQueue` reverts for that asset, making it impossible to ever process the locked request. The rsETH remains in the contract indefinitely with no user-callable path to retrieve it.

There is no `cancelWithdrawal` or equivalent function anywhere in the contract. The only cleanup path, `sweepRemainingAssets`, sweeps LST balances to the treasury and is gated by `onlyLRTManager` — it does not return rsETH to users and cannot be called by users at all. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

A user who calls `initiateWithdrawal` surrenders their rsETH to the contract. If the corresponding asset is subsequently deprecated (removed from `lrtConfig` supported assets), `unlockQueue` becomes permanently uncallable for that asset due to the `onlySupportedAsset` modifier. The user's rsETH is permanently frozen in the contract with no on-chain recovery path. Even without asset deprecation, indefinite protocol pause or operator inaction produces the same outcome. This meets the **Critical — Permanent freezing of funds** impact threshold. [4](#0-3) [5](#0-4) 

---

### Likelihood Explanation

The trigger requires one of several realistic conditions: (1) an asset is deprecated after users have queued withdrawals for it — a normal protocol lifecycle event; (2) the contract is paused for an extended period while locked requests accumulate; (3) the `LRTUnstakingVault` has insufficient assets to cover the committed amount, causing `unlockQueue` to break out of its loop without processing older requests. All three are plausible without any attacker involvement. Any rsETH holder can reach this state by calling the public `initiateWithdrawal` entry point. [6](#0-5) 

---

### Recommendation

Add a user-callable `cancelWithdrawal` function that:
1. Verifies the caller owns the oldest locked (not yet unlocked) request for the given asset.
2. Removes the request from `userAssociatedNonces` and `withdrawalRequests`.
3. Decrements `assetsCommitted[asset]` by `request.expectedAssetAmount`.
4. Returns `request.rsETHUnstaked` to the caller via `IERC20(lrtConfig.rsETH()).safeTransfer(msg.sender, request.rsETHUnstaked)`.

This mirrors the fix described in the reference report: extend the cleanup path to return the deposited funds to their rightful owner. [7](#0-6) 

---

### Proof of Concept

1. User calls `initiateWithdrawal(stETH, 10e18, "")`. Contract receives 10 rsETH; `assetsCommitted[stETH] += X`; request stored at nonce N.
2. Protocol governance removes stETH from supported assets (normal deprecation).
3. Operator attempts `unlockQueue(stETH, ...)` → reverts at `onlySupportedAsset(stETH)` modifier.
4. User attempts `completeWithdrawal(stETH, "")` → reverts at `WithdrawalLocked` because `nextLockedNonce` was never advanced.
5. User has no `cancelWithdrawal` to call. The 10 rsETH is permanently locked in `LRTWithdrawalManager`. [8](#0-7) [4](#0-3)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L71-76)
```text
    modifier onlySupportedStrategy(address asset) {
        if (asset != LRTConstants.ETH_TOKEN && lrtConfig.assetStrategy(asset) == address(0)) {
            revert StrategyNotSupported();
        }
        _;
    }
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

**File:** contracts/LRTWithdrawalManager.sol (L268-305)
```text
    function unlockQueue(
        address asset,
        uint256 firstExcludedIndex,
        uint256 minimumAssetPrice,
        uint256 minimumRsEthPrice,
        uint256 maximumAssetPrice,
        uint256 maximumRsEthPrice
    )
        external
        nonReentrant
        onlySupportedAsset(asset)
        whenNotPaused
        onlyAssetTransferOrOperatorRole
        returns (uint256 rsETHBurned, uint256 assetAmountUnlocked)
    {
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));

        UnlockParams memory params = _createUnlockParams(lrtOracle, unstakingVault, asset);

        _validatePrices(
            params.rsETHPrice,
            params.assetPrice,
            minimumRsEthPrice,
            maximumRsEthPrice,
            minimumAssetPrice,
            maximumAssetPrice
        );

        if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero();

        // Updates and unlocks withdrawal requests up to a specified upper limit or until allocated assets are fully
        // utilized.
        (rsETHBurned, assetAmountUnlocked) = _unlockWithdrawalRequests(
            asset, params.totalAvailableAssets, params.rsETHPrice, params.assetPrice, firstExcludedIndex
        );

        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
```

**File:** contracts/LRTWithdrawalManager.sol (L395-414)
```text
    function sweepRemainingAssets(address asset)
        external
        nonReentrant
        onlySupportedAsset(asset)
        onlyLRTManager
        returns (uint256 transferredAmount)
    {
        // Check that all withdrawals are completed
        if (hasUnlockedWithdrawals(asset)) revert PendingWithdrawalsExist();

        uint256 balance = _getAssetBalance(asset);
        if (balance == 0) revert AmountMustBeGreaterThanZero();

        // Transfer to treasury
        address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        _transferAsset(asset, treasury, balance);

        emit RemainingAssetsSwept(asset, balance, treasury);
        return balance;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L699-707)
```text
    function _processWithdrawalCompletion(address asset, address user, string calldata referralId) internal {
        if (userAssociatedNonces[asset][user].empty()) {
            revert NoWithdrawalRequests(user, asset);
        }

        // Retrieve and remove the oldest withdrawal request for the user.
        uint256 usersFirstWithdrawalRequestNonce = userAssociatedNonces[asset][user].popFront();
        // Ensure the request is already unlocked.
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
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

**File:** contracts/LRTWithdrawalManager.sol (L790-800)
```text
        while (nextLockedNonce_ < firstExcludedIndex) {
            bytes32 requestId = getRequestId(asset, nextLockedNonce_);
            WithdrawalRequest storage request = withdrawalRequests[requestId];

            // Check that the withdrawal delay has passed since the request's initiation.
            if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;

            // Calculate the amount user will receive
            uint256 payoutAmount = _calculatePayoutAmount(request, rsETHPrice, assetPrice);

            if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request
```
