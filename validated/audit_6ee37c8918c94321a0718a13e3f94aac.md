Audit Report

## Title
ETH Permanently Frozen in `LRTWithdrawalManager` When Withdrawal Recipient Cannot Receive ETH - (File: contracts/LRTWithdrawalManager.sol)

## Summary
The two-step withdrawal lifecycle separates rsETH burning (`unlockQueue`) from ETH delivery (`completeWithdrawal`). If the withdrawing address is a smart contract without a working `receive()`/`fallback()`, the ETH push in `_processWithdrawalCompletion` reverts on every attempt. Because the rsETH burn occurred in a prior, already-finalized transaction, the ETH is permanently locked inside `LRTWithdrawalManager` with no admin recovery path.

## Finding Description
**Step 1 – `initiateWithdrawal` (L150–178):** No restriction on contract callers; any address including smart contracts can deposit rsETH. [1](#0-0) 

**Step 2 – `unlockQueue` (L305–307):** In a separate, finalized transaction, rsETH is burned and ETH is redeemed from `LRTUnstakingVault` into `LRTWithdrawalManager`. This is irreversible. [2](#0-1) 

**Step 3 – `_processWithdrawalCompletion` (L699–738):** The function pops the nonce, deletes the request, decrements `unlockedWithdrawalsCount`, then calls `_transferAsset`. If `_transferAsset` reverts, the entire transaction reverts — restoring the nonce, the request, and the count — but the rsETH burn from step 2 is not undone. [3](#0-2) 

**Root cause – `_transferAsset` (L876–883):** ETH is pushed via a raw `call{value}`. If `to` has no `receive()`/`fallback()` or one that reverts, `sent == false` and `EthTransferFailed` is thrown, reverting the entire completion transaction. [4](#0-3) 

**Admin escape hatch blocked – `sweepRemainingAssets` (L403):** The only ETH-recovery function is gated on `hasUnlockedWithdrawals(asset) == false`. Because the failed `completeWithdrawal` always reverts (restoring `unlockedWithdrawalsCount > 0`), this gate can never be passed for the affected asset. [5](#0-4) 

No other ETH-recovery function exists in the contract.

## Impact Explanation
**Critical – Permanent freezing of funds.** The ETH redeemed from `LRTUnstakingVault` on behalf of the affected user is irrecoverably locked inside `LRTWithdrawalManager`. The user's rsETH is already burned. Neither the user, operator, nor any admin can retrieve the ETH through any supported code path.

## Likelihood Explanation
**Low.** The scenario requires a smart contract that (a) holds rsETH and can call `initiateWithdrawal`, and (b) has no working `receive()`/`fallback()`. This is a realistic pattern for DeFi vaults, multisigs with custom guards, or proxy contracts that hold rsETH but were not designed to accept raw ETH. The protocol places no restriction on contract callers in `initiateWithdrawal`. [6](#0-5) 

## Recommendation
1. **Separate the burn from delivery:** Delay the rsETH burn until `completeWithdrawal` succeeds, so a failed delivery does not result in burned rsETH with no corresponding ETH receipt.
2. **Allow a recipient override:** Let the initiating address specify a separate `recipient` for the ETH payout, enabling contracts to redirect funds to an EOA or ETH-capable address.
3. **Add an ungated admin ETH-recovery path:** Provide a function callable by an admin that can recover ETH for a specific withdrawal request that is provably undeliverable, not gated on `unlockedWithdrawalsCount`.

## Proof of Concept
```
1. Deploy ContractDepositor: can call initiateWithdrawal, has no receive() function.
2. ContractDepositor acquires rsETH and calls:
       LRTWithdrawalManager.initiateWithdrawal(ETH_TOKEN, rsETHAmount, "")
   → rsETH transferred to LRTWithdrawalManager.
3. Operator calls:
       LRTWithdrawalManager.unlockQueue(ETH_TOKEN, ...)
   → rsETH burned (irreversible); ETH moved from LRTUnstakingVault to LRTWithdrawalManager.
4. ContractDepositor calls:
       LRTWithdrawalManager.completeWithdrawal(ETH_TOKEN, "")
   → _transferAsset sends ETH to ContractDepositor via call{value}
   → ContractDepositor has no receive(); call returns false
   → revert EthTransferFailed()
   → All state in this tx reverts; withdrawal request and unlockedWithdrawalsCount restored.
5. Repeat step 4 indefinitely → always reverts.
6. Admin attempts sweepRemainingAssets(ETH_TOKEN):
   → hasUnlockedWithdrawals(ETH_TOKEN) == true → revert PendingWithdrawalsExist()
7. ETH is permanently locked. rsETH is permanently burned.
```

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L155-166)
```text
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
```

**File:** contracts/LRTWithdrawalManager.sol (L305-307)
```text
        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTWithdrawalManager.sol (L403-403)
```text
        if (hasUnlockedWithdrawals(asset)) revert PendingWithdrawalsExist();
```

**File:** contracts/LRTWithdrawalManager.sol (L705-734)
```text
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
