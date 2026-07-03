### Title
ETH Withdrawal Permanently Frozen for Contracts Unable to Receive ETH — (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

`LRTWithdrawalManager._transferAsset` sends ETH directly to the requesting user's address with no fallback path. If the user is a smart contract that cannot receive ETH (e.g., a multisig with no `receive()` function or one that reverts), every call to `completeWithdrawal` reverts. Because the user's rsETH is already burned by the time `completeWithdrawal` is called, the ETH is permanently locked in the contract with no alternative withdrawal path.

---

### Finding Description

The withdrawal lifecycle in `LRTWithdrawalManager` is split across two separate transactions:

**Step 1 — `initiateWithdrawal`:** The user transfers rsETH to the contract. [1](#0-0) 

**Step 2 — `unlockQueue` (operator-only):** The rsETH held by the contract is burned and ETH is redeemed from the unstaking vault into `LRTWithdrawalManager`. [2](#0-1) 

**Step 3 — `completeWithdrawal`:** The user calls this to receive their ETH. It delegates to `_processWithdrawalCompletion`, which calls `_transferAsset(asset, user, request.expectedAssetAmount)`. [3](#0-2) 

The `_transferAsset` function for ETH is:

```solidity
function _transferAsset(address asset, address to, uint256 amount) internal {
    if (asset == LRTConstants.ETH_TOKEN) {
        (bool sent,) = payable(to).call{ value: amount }("");
        if (!sent) revert EthTransferFailed();
    } else {
        IERC20(asset).safeTransfer(to, amount);
    }
}
``` [4](#0-3) 

If `to` is a contract that cannot receive ETH, `sent` is `false` and the function reverts with `EthTransferFailed()`. Because the revert unwinds all state changes in that call, the withdrawal request record is restored. However, the rsETH burned in Step 2 is **not** restored — that burn happened in a prior, already-finalized transaction.

There is no `withdrawTo(address recipient)` or any other function that allows the user to redirect their ETH to a different address. `completeWithdrawalForUser` (the operator variant) also hardcodes the destination as `user`: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

After `unlockQueue` burns the user's rsETH, the ETH owed to that user sits in `LRTWithdrawalManager`. If the user's address cannot receive ETH, every subsequent `completeWithdrawal` call reverts. The user has permanently lost their rsETH and cannot recover the corresponding ETH. This constitutes **permanent freezing of funds** with no on-chain recovery path short of a contract upgrade.

**Impact: Critical — Permanent freezing of funds.**

---

### Likelihood Explanation

Smart contract wallets (Gnosis Safe multisigs, account-abstraction wallets, protocol treasuries) are common participants in DeFi withdrawal flows. A multisig with non-trivial receive logic, or a contract with no `receive()` function at all, will silently fail every ETH push. The `completeWithdrawalForUser` comment even acknowledges this class of issue for ETH: [7](#0-6) 

Any such user who initiates an ETH withdrawal and whose request is subsequently unlocked by the operator is permanently affected.

**Likelihood: Medium** — requires the withdrawing address to be a contract that cannot receive ETH, which is realistic for multisigs and protocol-owned addresses.

---

### Recommendation

Add a `completeWithdrawalTo(address asset, address recipient, string calldata referralId)` function that allows `msg.sender` to redirect their ETH to an arbitrary `recipient` address. This mirrors the `withdrawTo` mitigation suggested in the referenced report. The key invariant is that only the original requester (`msg.sender`) can choose the redirect target, preserving fund attribution:

```solidity
function completeWithdrawalTo(
    address asset,
    address payable recipient,
    string calldata referralId
) external nonReentrant whenNotPaused {
    _processWithdrawalCompletionTo(asset, msg.sender, recipient, referralId);
}
```

Where `_processWithdrawalCompletionTo` is a variant of `_processWithdrawalCompletion` that calls `_transferAsset(asset, recipient, amount)` instead of `_transferAsset(asset, user, amount)`.

---

### Proof of Concept

1. A Gnosis Safe multisig (`userSafe`) holds rsETH and calls `initiateWithdrawal(ETH_TOKEN, rsETHAmount)`. rsETH is transferred to `LRTWithdrawalManager`. [1](#0-0) 

2. The operator calls `unlockQueue(ETH_TOKEN, ...)`. The rsETH is burned from the contract and ETH is redeemed into `LRTWithdrawalManager`. [2](#0-1) 

3. `userSafe` calls `completeWithdrawal(ETH_TOKEN, "")`. Inside `_processWithdrawalCompletion`, `_transferAsset(ETH_TOKEN, userSafe, amount)` executes `payable(userSafe).call{value: amount}("")`. The Safe's fallback reverts (e.g., requires a specific payload or has no receive function). `sent == false`, so `EthTransferFailed()` is thrown. [8](#0-7) 

4. The transaction reverts. The withdrawal request record is restored. The rsETH burn from Step 2 is permanent. `userSafe` retries indefinitely — every attempt reverts. ETH is permanently locked in `LRTWithdrawalManager`.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-166)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L191-191)
```text
    /// @dev Not expected to be used for ETH; potential gas grief scenarios are non-impactful for ETH
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

**File:** contracts/interfaces/ILRTWithdrawalManager.sol (L96-98)
```text
    function completeWithdrawal(address asset, string calldata referralId) external;

    function completeWithdrawalForUser(address asset, address user, string calldata referralId) external;
```
