### Title
Contract Withdrawers Unable to Receive ETH Have Funds Permanently Frozen After rsETH Is Burned - (File: contracts/LRTWithdrawalManager.sol)

---

### Summary

When a smart contract initiates an ETH withdrawal via `initiateWithdrawal`, the rsETH is burned during `unlockQueue` before the user claims. If the withdrawing contract has no `receive()` function, every subsequent call to `completeWithdrawal` reverts, leaving the ETH permanently locked in `LRTWithdrawalManager` with no mechanism to redirect it to an alternative address.

---

### Finding Description

The withdrawal lifecycle in `LRTWithdrawalManager` proceeds in two distinct phases:

**Phase 1 — `initiateWithdrawal`** (user-triggered):
rsETH is transferred from `msg.sender` into the withdrawal manager and a `WithdrawalRequest` is stored keyed to `msg.sender`. [1](#0-0) 

**Phase 2 — `unlockQueue`** (operator-triggered):
The operator burns the rsETH held by the contract and pulls the corresponding ETH from `LRTUnstakingVault` into the withdrawal manager. After this call, the rsETH is **permanently destroyed**. [2](#0-1) 

**Phase 3 — `completeWithdrawal`** (user-triggered):
`_processWithdrawalCompletion` is called with `user = msg.sender`, which always attempts to push ETH to the original requester's address via a low-level call. [3](#0-2) [4](#0-3) [5](#0-4) 

If `user` is a smart contract without a `receive()` or `fallback()` function, the `.call{value: amount}("")` returns `false`, and the function reverts with `EthTransferFailed`. Because the revert undoes the `delete withdrawalRequests[requestId]` and the `popFront()`, the withdrawal request remains in the queue — but the rsETH was already burned in Phase 2 and cannot be restored. [6](#0-5) 

The operator-accessible `completeWithdrawalForUser` does not help: it also sends to the same hardcoded `user` address, and its own NatSpec comment acknowledges ETH delivery issues ("Not expected to be used for ETH"). [7](#0-6) 

There is no function that allows the user to specify an alternative recipient address, and `sweepRemainingAssets` cannot be used because `hasUnlockedWithdrawals` returns `true` while the stuck request exists. [8](#0-7) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

The user's rsETH is irreversibly burned during `unlockQueue`. The ETH allocated to them is held in the withdrawal manager but can never be delivered because every `completeWithdrawal` call reverts. There is no escape hatch: no `to` parameter, no position-transfer function, and no admin sweep path while the unlocked withdrawal exists. Both the burned rsETH and the locked ETH are permanently lost to the user.

---

### Likelihood Explanation

**Medium.** Smart contracts that hold rsETH and interact with the withdrawal system are a realistic and expected use case: protocol-owned vaults, DAO treasuries, yield aggregators, and multisig-controlled strategies all commonly hold liquid restaking tokens. Many such contracts omit a `receive()` function by design (e.g., pure ERC-20 vaults, proxy contracts whose implementation does not accept ETH). No special privilege or front-running is required — the user simply needs to call `initiateWithdrawal` from such a contract.

---

### Recommendation

Add a `recipient` parameter to `initiateWithdrawal` (or to `completeWithdrawal`) so the caller can designate a separate ETH-receiving address at the time of initiation or claim:

```solidity
function initiateWithdrawal(
    address asset,
    uint256 rsETHUnstaked,
    address recipient,       // <-- new: address that will receive the asset
    string calldata referralId
) external ...
```

Store `recipient` inside `WithdrawalRequest` and use it in `_processWithdrawalCompletion` instead of `user`. This mirrors the fix applied in BullvBear PR#14, which added a `transferPosition` mechanism so that a position owner can redirect delivery to any EOA or capable contract before claiming.

Alternatively, add a `transferWithdrawalRecipient(address asset, uint256 nonce, address newRecipient)` function that lets the current owner of a withdrawal request redirect its delivery address, analogous to `transferPosition` in BullvBear.

---

### Proof of Concept

1. A vault contract `VaultContract` (no `receive()`) holds rsETH and calls:
   ```solidity
   LRTWithdrawalManager.initiateWithdrawal(ETH_TOKEN, rsETHAmount, "");
   ```
   rsETH is transferred from `VaultContract` to the withdrawal manager.
   `withdrawalRequests[requestId]` is created with `userAssociatedNonces[ETH][VaultContract]`.

2. Operator calls `unlockQueue(ETH_TOKEN, ...)`.
   Line 305 executes: `IRSETH(...).burnFrom(address(this), rsETHBurned)` — rsETH is **permanently burned**.
   ETH is pulled from `LRTUnstakingVault` into `LRTWithdrawalManager`.

3. `VaultContract` calls `completeWithdrawal(ETH_TOKEN, "")`.
   `_processWithdrawalCompletion` reaches line 734:
   ```solidity
   _transferAsset(ETH_TOKEN, VaultContract, amount);
   ```
   Inside `_transferAsset`:
   ```solidity
   (bool sent,) = payable(VaultContract).call{ value: amount }("");
   // sent == false — VaultContract has no receive()
   if (!sent) revert EthTransferFailed();   // entire tx reverts
   ```

4. The revert restores `withdrawalRequests[requestId]` and the nonce queue. The withdrawal request is permanently unclaimable. The rsETH burned in step 2 is gone. The ETH in the withdrawal manager is permanently frozen for `VaultContract` with no redirect path.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-175)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);
```

**File:** contracts/LRTWithdrawalManager.sol (L183-185)
```text
    function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
        _processWithdrawalCompletion(asset, msg.sender, referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L192-203)
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
```

**File:** contracts/LRTWithdrawalManager.sol (L305-307)
```text
        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);
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

**File:** contracts/LRTWithdrawalManager.sol (L705-712)
```text
        uint256 usersFirstWithdrawalRequestNonce = userAssociatedNonces[asset][user].popFront();
        // Ensure the request is already unlocked.
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();

        bytes32 requestId = getRequestId(asset, usersFirstWithdrawalRequestNonce);
        WithdrawalRequest memory request = withdrawalRequests[requestId];

        delete withdrawalRequests[requestId];
```

**File:** contracts/LRTWithdrawalManager.sol (L734-734)
```text
        _transferAsset(asset, user, request.expectedAssetAmount);
```

**File:** contracts/LRTWithdrawalManager.sol (L876-883)
```text
    function _transferAsset(address asset, address to, uint256 amount) internal {
        if (asset == LRTConstants.ETH_TOKEN) {
            (bool sent,) = payable(to).call{ value: amount }("");
            if (!sent) revert EthTransferFailed();
        } else {
            IERC20(asset).safeTransfer(to, amount);
        }
    }
```
