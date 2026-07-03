### Title
Fee Recipient Revert Blocks All ETH Instant Withdrawals - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager.instantWithdrawal` transfers the fee to `feeRecipient` **before** transferring the user's ETH. If `feeRecipient` is a contract that reverts on ETH receipt (compromised, sanctioned, or otherwise non-receivable), every ETH instant withdrawal call reverts, permanently freezing the instant-withdrawal path for all users until an admin intervenes.

### Finding Description
`instantWithdrawal` follows this sequence for ETH withdrawals:

1. Burns the caller's rsETH (line 229).
2. Redeems the asset from the unstaking vault (line 235).
3. Computes `fee` and `userAmount` (lines 237–238).
4. Resolves `feeRecipient` — either `instantWithdrawalFeeRecipient` or, when unset, the `PROTOCOL_TREASURY` from `lrtConfig` (lines 240–244).
5. Calls `_transferAsset(asset, feeRecipient, fee)` (line 246).
6. Calls `_transferAsset(asset, msg.sender, userAmount)` (line 250).

`_transferAsset` for ETH uses a low-level call with a hard revert on failure:

```solidity
(bool sent,) = payable(to).call{ value: amount }("");
if (!sent) revert EthTransferFailed();
```

If `feeRecipient` is a contract that cannot receive ETH (no `receive()` / `fallback()`, or one that deliberately reverts), step 5 reverts with `EthTransferFailed`, and step 6 — the user's transfer — is never reached. Because the entire transaction reverts, the rsETH burn and vault redeem are also rolled back, so the user does not lose tokens, but the call fails unconditionally and cannot be retried until the fee recipient is changed.

The `feeRecipient` address is set by `LRTManager` via `setInstantWithdrawalFeeRecipient`, or defaults to `PROTOCOL_TREASURY`. Either address could be a contract that later becomes compromised, sanctioned, or otherwise unable to accept ETH — exactly the scenario described in M-01.

### Impact Explanation
All ETH instant withdrawals are blocked for every user while the fee recipient is in a reverting state. Users holding rsETH who wish to exit via the instant path cannot do so. This constitutes a **temporary freezing of funds** (Medium severity) until an admin replaces the fee recipient.

### Likelihood Explanation
Low. The fee recipient must be a contract that reverts on ETH receipt. This can occur if the recipient contract is upgraded to remove its `receive()` function, is sanctioned (causing an on-chain compliance hook to revert), or is otherwise compromised. The LRT Manager can remediate by calling `setInstantWithdrawalFeeRecipient` with a working address, but the window between the recipient becoming non-functional and the admin fix can be significant.

### Recommendation
Mirror the M-01 recommendation: do not revert on fee-transfer failure. Instead, accumulate uncollected fees in a storage variable and allow the fee recipient to pull them separately:

```solidity
if (fee > 0) {
    (bool sent,) = payable(feeRecipient).call{ value: fee }("");
    if (!sent) {
        pendingFees[feeRecipient] += fee;  // accrue for later pull
    } else {
        emit InstantWithdrawalFeeCollected(msg.sender, asset, fee);
    }
}
_transferAsset(asset, msg.sender, userAmount);
```

This ensures the user's withdrawal always succeeds regardless of the fee recipient's state.

### Proof of Concept

1. LRT Manager calls `setInstantWithdrawalFeeRecipient(address(revertingContract))` where `revertingContract` has no `receive()`.
2. User calls `instantWithdrawal(ETH_TOKEN, rsETHAmount, "")`.
3. Execution reaches line 246: `_transferAsset(ETH_TOKEN, revertingContract, fee)`.
4. The low-level call returns `sent = false`; `EthTransferFailed` is thrown.
5. The entire transaction reverts — user receives no ETH and cannot retry.
6. All subsequent ETH instant withdrawal calls by any user revert identically until the fee recipient is replaced.

Relevant code: [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L237-252)
```text
        uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
        uint256 userAmount = assetAmountUnlocked - fee;

        address feeRecipient = instantWithdrawalFeeRecipient;
        if (feeRecipient == address(0)) {
            // Backwards-compatible default: send fees to the protocol treasury
            feeRecipient = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        }
        if (fee > 0) {
            _transferAsset(asset, feeRecipient, fee);
            emit InstantWithdrawalFeeCollected(msg.sender, asset, fee);
        }

        _transferAsset(asset, msg.sender, userAmount);
        emit ReferralIdEmitted(referralId);
        emit AssetWithdrawalFinalized(msg.sender, asset, rsETHUnstaked, userAmount);
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
