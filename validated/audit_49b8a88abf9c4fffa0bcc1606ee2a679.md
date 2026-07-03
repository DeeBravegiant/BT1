### Title
Push-Pattern ETH Transfer in `_processWithdrawalCompletion` Permanently Freezes User Funds - (File: contracts/LRTWithdrawalManager.sol)

---

### Summary

`LRTWithdrawalManager` uses a push pattern to deliver ETH to withdrawing users. If the recipient is a contract that cannot accept ETH, the transfer reverts and the withdrawal is permanently uncompletable. By that point the user's rsETH has already been burned and the ETH equivalent is held in the contract with no alternative recovery path.

---

### Finding Description

`_processWithdrawalCompletion` is the single code path executed by both `completeWithdrawal` (user-initiated) and `completeWithdrawalForUser` (operator-initiated). For ETH withdrawals it ends with a push transfer to the user address:

```solidity
// contracts/LRTWithdrawalManager.sol L876-L883
function _transferAsset(address asset, address to, uint256 amount) internal {
    if (asset == LRTConstants.ETH_TOKEN) {
        (bool sent,) = payable(to).call{ value: amount }("");
        if (!sent) revert EthTransferFailed();
    } else {
        IERC20(asset).safeTransfer(to, amount);
    }
}
``` [1](#0-0) 

Both public completion functions route through the same internal function and push to the same `user` address:

```solidity
// L183-L203
function completeWithdrawal(address asset, ...) external ... {
    _processWithdrawalCompletion(asset, msg.sender, referralId);
}
function completeWithdrawalForUser(address asset, address user, ...) external ... onlyLRTOperator {
    _processWithdrawalCompletion(asset, user, referralId);   // still pushes to `user`
}
``` [2](#0-1) 

The NatSpec on `completeWithdrawalForUser` even acknowledges the limitation: *"Not expected to be used for ETH"*, confirming there is no operator escape hatch for ETH. [3](#0-2) 

The lifecycle that leads to permanent loss:

1. **`initiateWithdrawal`** — rsETH is transferred from the user into the contract. [4](#0-3) 

2. **`unlockQueue`** — rsETH held by the contract is **burned** and the ETH equivalent is pulled from the unstaking vault into the withdrawal manager. [5](#0-4) 

3. **`completeWithdrawal` / `completeWithdrawalForUser`** — ETH is pushed to `user`. If `user` is a contract that reverts on `receive`, the call fails, the transaction reverts, and the request remains in the queue. Every future attempt produces the same revert. The ETH is permanently stranded in `LRTWithdrawalManager`.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

After step 2 the rsETH is irreversibly burned. The ETH equivalent sits in the withdrawal manager and can never be delivered because the only delivery mechanism is the push call that always reverts for this user. There is no admin function that redirects an ETH withdrawal to an alternative address, and `sweepRemainingAssets` explicitly states it is *"Not expected to be used for ETH"*. [6](#0-5) 

---

### Likelihood Explanation

**Medium.** Any smart-contract wallet, multisig, or protocol contract that holds rsETH and initiates an ETH withdrawal is at risk if its `receive` function is absent or reverts. This is a realistic scenario: multisigs (e.g., Gnosis Safe) do accept ETH, but custom vaults, yield aggregators, or contracts with a reverting fallback do not. No attacker action is required — the user simply needs to be a contract that cannot receive ETH.

---

### Recommendation

Replace the push pattern with a pull pattern for ETH withdrawals:

1. Instead of calling `payable(user).call{value: amount}("")` inside `_processWithdrawalCompletion`, record the claimable amount in a mapping (e.g., `pendingETHWithdrawals[user] += amount`).
2. Expose a separate `claimETH()` function that lets the user (or any address on their behalf) withdraw to an arbitrary recipient address they specify.

This mirrors the fix described in the referenced Moloch report and eliminates the dependency on the recipient's ability to accept a pushed ETH transfer.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// Attacker/victim: a contract that cannot receive ETH
contract NoReceiveETH {
    ILRTWithdrawalManager withdrawalManager;
    IERC20 rsETH;

    constructor(address _wm, address _rsETH) {
        withdrawalManager = ILRTWithdrawalManager(_wm);
        rsETH = IERC20(_rsETH);
    }

    function initiateETHWithdrawal(uint256 rsETHAmount) external {
        rsETH.approve(address(withdrawalManager), rsETHAmount);
        // Step 1: rsETH transferred to contract, withdrawal request created
        withdrawalManager.initiateWithdrawal(ETH_TOKEN, rsETHAmount, "");
    }

    // After operator calls unlockQueue (rsETH burned, ETH in withdrawal manager):
    function tryComplete() external {
        // Step 3: always reverts with EthTransferFailed because this contract
        // has no receive() function — ETH push to address(this) fails
        withdrawalManager.completeWithdrawal(ETH_TOKEN, "");
    }

    // Operator calling completeWithdrawalForUser also reverts for the same reason.
    // rsETH is burned, ETH is permanently stuck in LRTWithdrawalManager.
}
```

The `_transferAsset` push at line 878 reverts, `EthTransferFailed` is thrown, and the ETH remains in `LRTWithdrawalManager` indefinitely with no admin recovery path. [7](#0-6)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-166)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L183-203)
```text
    function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
        _processWithdrawalCompletion(asset, msg.sender, referralId);
    }

    /// @notice Allows operators to complete a user's withdrawal process
    /// @param asset The asset address the user wishes to withdraw
    /// @param user The address of the user whose withdrawal to complete
    /// @param referralId The referral identifier for tracking
    /// @dev Not expected to be used for ETH; potential gas grief scenarios are non-impactful for ETH
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

**File:** contracts/LRTWithdrawalManager.sol (L394-394)
```text
    /// @dev Not expected to be used for ETH; ETH should not accumulate requiring sweeping
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
