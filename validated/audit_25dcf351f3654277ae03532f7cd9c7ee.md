### Title
Unresponsive Operator Permanently Freezes User Withdrawal Requests — (File: `contracts/LRTWithdrawalManager.sol`)

---

### Summary

The `unlockQueue()` function in `LRTWithdrawalManager` is gated behind `onlyAssetTransferOrOperatorRole`. Users who call `initiateWithdrawal()` transfer their rsETH into the contract immediately, but can never complete the withdrawal unless an operator first calls `unlockQueue()` to advance `nextLockedNonce`. If the operator role becomes unresponsive (key loss, operational failure, etc.), all queued withdrawal requests are permanently frozen with no user-accessible escape path.

---

### Finding Description

The withdrawal lifecycle in `LRTWithdrawalManager` is a two-step process:

**Step 1 — User initiates withdrawal:**
`initiateWithdrawal()` transfers rsETH from the user into the contract and records a `WithdrawalRequest`. [1](#0-0) 

**Step 2 — Operator unlocks the queue:**
`unlockQueue()` is the only function that advances `nextLockedNonce[asset]`, which is the gate that determines whether a request is claimable. [2](#0-1) 

`nextLockedNonce` is advanced exclusively inside `_unlockWithdrawalRequests`, called only from `unlockQueue()`: [3](#0-2) 

**Step 3 — User attempts to complete withdrawal:**
`_processWithdrawalCompletion` enforces that the request nonce is below `nextLockedNonce`. If the operator never called `unlockQueue()`, this check always reverts: [4](#0-3) 

There is **no `cancelWithdrawal` or rsETH reclaim function** anywhere in the contract. The only other completion path, `completeWithdrawalForUser()`, is also restricted to `onlyLRTOperator`: [5](#0-4) 

The `instantWithdrawal()` path is a separate entry point that does not help users who have already called `initiateWithdrawal()` — it requires a fresh rsETH burn and is separately gated by `isInstantWithdrawalEnabled[asset]`.

---

### Impact Explanation

**Permanent freezing of funds (Critical).**

Once a user calls `initiateWithdrawal()`, their rsETH is held in the contract. If the operator role becomes permanently unresponsive (key loss, infrastructure failure), `nextLockedNonce` is never advanced, `completeWithdrawal()` always reverts with `WithdrawalLocked`, and there is no user-accessible escape hatch. The rsETH is irrecoverably locked in the contract. All `assetsCommitted` accounting is also permanently skewed, blocking future depositors from initiating withdrawals for those assets. [6](#0-5) 

---

### Likelihood Explanation

**Low-to-Medium.** The operator role is a hot-wallet key used for routine protocol operations. Key loss, infrastructure outage, or a prolonged operational failure are realistic scenarios for any long-lived protocol. The Audius protocol accepted an identical finding (H12) as valid. The impact is catastrophic for any user who has already submitted a withdrawal request when the failure occurs.

---

### Recommendation

1. **Make `unlockQueue()` permissionless** (or callable by any address), analogous to the fix applied in Audius PR #556. Price bounds (`minimumAssetPrice`, `maximumRsEthPrice`, etc.) already protect against oracle manipulation, so removing the role restriction does not introduce new attack surfaces.
2. **Alternatively**, add a `cancelWithdrawal()` function that allows users to reclaim their rsETH if their request has not been unlocked within a defined timeout period.

---

### Proof of Concept

1. Alice calls `initiateWithdrawal(stETH, 1e18)`. Her 1 rsETH is transferred to `LRTWithdrawalManager`. `nextUnusedNonce[stETH]` becomes 1; `nextLockedNonce[stETH]` remains 0.
2. The operator key is lost. `unlockQueue()` is never called. `nextLockedNonce[stETH]` stays at 0 forever.
3. Alice calls `completeWithdrawal(stETH, "")`. Inside `_processWithdrawalCompletion`:
   - `usersFirstWithdrawalRequestNonce = 0`
   - Check: `0 >= nextLockedNonce[stETH]` → `0 >= 0` → **true** → `revert WithdrawalLocked()`
4. Alice has no other function to call. Her rsETH is permanently locked in the contract. [7](#0-6)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-176)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);

```

**File:** contracts/LRTWithdrawalManager.sol (L192-204)
```text
    function completeWithdrawalForUser(
        address asset,
        address user,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlyLRTOperator
    {
        _processWithdrawalCompletion(asset, user, referralId);
        emit AssetWithdrawalCompletedBy(msg.sender);
    }
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

**File:** contracts/LRTWithdrawalManager.sol (L699-715)
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
```

**File:** contracts/LRTWithdrawalManager.sol (L810-815)
```text

            unchecked {
                nextLockedNonce_++;
            }
        }
        nextLockedNonce[asset] = nextLockedNonce_;
```
