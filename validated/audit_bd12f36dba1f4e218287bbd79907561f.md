### Title
Users Cannot Cancel Withdrawal Requests — rsETH Permanently Frozen If Operator Fails to Call `unlockQueue` - (File: contracts/LRTWithdrawalManager.sol)

---

### Summary

`LRTWithdrawalManager` requires a privileged operator to call `unlockQueue()` before any user can complete a withdrawal. Once a user calls `initiateWithdrawal()`, their rsETH is transferred into the contract and there is no mechanism to cancel the request or reclaim the rsETH. If the operator never calls `unlockQueue()`, user funds are permanently frozen with no escape hatch.

---

### Finding Description

The withdrawal lifecycle in `LRTWithdrawalManager` is a two-step process:

**Step 1 — User initiates (rsETH locked):**

`initiateWithdrawal()` pulls rsETH from the user into the contract and enqueues a request with a nonce equal to `nextUnusedNonce[asset]`, which is always `>= nextLockedNonce[asset]` at the time of creation. [1](#0-0) 

**Step 2 — Operator must unlock (privileged gate):**

`unlockQueue()` is restricted to `onlyAssetTransferOrOperatorRole`. It advances `nextLockedNonce[asset]` past the user's request nonce, which is the only way to make a request claimable. [2](#0-1) 

**Step 3 — User completes (blocked without Step 2):**

`_processWithdrawalCompletion()` enforces that the user's nonce is strictly less than `nextLockedNonce[asset]`. If the operator never called `unlockQueue()`, this check always reverts with `WithdrawalLocked`. [3](#0-2) 

**No cancel path exists.** A full audit of the contract confirms there is no `cancelWithdrawal()` or equivalent function. The only other user-facing path is `instantWithdrawal()`, but that requires `isInstantWithdrawalEnabled[asset] == true` (a separate manager toggle) and burns rsETH directly from the caller's wallet — it does not recover rsETH already locked in a queued request. [4](#0-3) 

The `sweepRemainingAssets()` function is manager-only and sends funds to the treasury, not back to users. [5](#0-4) 

---

### Impact Explanation

If the operator holding `ASSET_TRANSFER_ROLE` or `OPERATOR_ROLE` fails to call `unlockQueue()` — due to operational failure, key loss, protocol shutdown, or any other reason — all rsETH locked in pending withdrawal requests is permanently frozen. Users have no on-chain recourse to recover their tokens.

This maps directly to the external report's vulnerability class: a required privileged action (submitting the private key / calling `unlockQueue`) is the sole gate between user funds and their recovery, with no timeout or user-initiated escape hatch.

**Impact:** Permanent freezing of funds (Critical) or at minimum temporary freezing of funds (Medium) depending on whether the operator eventually acts.

---

### Likelihood Explanation

The `ASSET_TRANSFER_ROLE` and `OPERATOR_ROLE` are protocol-controlled off-chain keys. [6](#0-5) 

Operational failures (key rotation errors, infrastructure outages, protocol deprecation) are realistic scenarios that do not require any malicious intent. Any user who has initiated a withdrawal is exposed to this risk for the entire duration their request remains in the locked queue.

---

### Recommendation

Add a user-callable `cancelWithdrawal()` function that allows a user to reclaim their rsETH if their request has not been unlocked within a defined timeout period (e.g., `withdrawalDelayBlocks` after initiation). This mirrors the fix applied in the referenced EMPAM report, where a `dedicatedSettlePeriod` was introduced after which bidders could claim refunds without the key holder's participation.

---

### Proof of Concept

1. Alice calls `initiateWithdrawal(stETH, 1e18, "")`. Her 1e18 rsETH is transferred to `LRTWithdrawalManager`. Her request is stored at nonce `N`, where `N >= nextLockedNonce[stETH]`. [7](#0-6) 

2. The operator never calls `unlockQueue(stETH, ...)`, so `nextLockedNonce[stETH]` remains `<= N`.

3. Alice calls `completeWithdrawal(stETH, "")`. Inside `_processWithdrawalCompletion`, the check `usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]` evaluates to `N >= N` → `true` → reverts with `WithdrawalLocked`. [8](#0-7) 

4. Alice has no other function to call. Her 1e18 rsETH is permanently locked in the contract with no recovery path.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L78-80)
```text
    modifier onlyInstantWithdrawalAllowed(address asset) {
        if (!isInstantWithdrawalEnabled[asset]) revert InstantWithdrawalNotEnabled();
        _;
```

**File:** contracts/LRTWithdrawalManager.sol (L166-175)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);
```

**File:** contracts/LRTWithdrawalManager.sol (L268-281)
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
```

**File:** contracts/LRTWithdrawalManager.sol (L395-413)
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
```

**File:** contracts/LRTWithdrawalManager.sol (L705-707)
```text
        uint256 usersFirstWithdrawalRequestNonce = userAssociatedNonces[asset][user].popFront();
        // Ensure the request is already unlocked.
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
```

**File:** contracts/LRTWithdrawalManager.sol (L750-757)
```text
        // Create and store the new withdrawal request.
        withdrawalRequests[requestId] = WithdrawalRequest({
            rsETHUnstaked: rsETHUnstaked, expectedAssetAmount: expectedAssetAmount, withdrawalStartBlock: block.number
        });

        // Map the user to the newly created request index and increment the nonce for future requests.
        userAssociatedNonces[asset][msg.sender].pushBack(nextUnusedNonce_);
        nextUnusedNonce[asset] = nextUnusedNonce_ + 1;
```

**File:** contracts/utils/LRTConfigRoleChecker.sol (L48-56)
```text
    modifier onlyAssetTransferOrOperatorRole() {
        if (
            !IAccessControl(address(lrtConfig)).hasRole(LRTConstants.ASSET_TRANSFER_ROLE, msg.sender)
                && !IAccessControl(address(lrtConfig)).hasRole(LRTConstants.OPERATOR_ROLE, msg.sender)
        ) {
            revert ILRTConfig.CallerNotLRTConfigOperatorOrAssetTransferRole();
        }
        _;
    }
```
