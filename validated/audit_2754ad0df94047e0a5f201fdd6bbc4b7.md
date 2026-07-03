### Title
Unlocked ETH Withdrawal Requests Cannot Be Cancelled When Aave Has Insufficient Liquidity, Temporarily Freezing User Funds - (File: contracts/LRTWithdrawalManager.sol)

---

### Summary

When the Aave v3 integration is enabled in `LRTWithdrawalManager`, the `unlockQueue` operator step burns the user's rsETH and deposits the corresponding ETH into Aave. If Aave subsequently has insufficient liquidity, `completeWithdrawal` reverts with `InsufficientLiquidityForWithdrawal`. Because no cancel mechanism exists for users, their ETH is temporarily frozen in Aave with no recourse until liquidity is restored.

---

### Finding Description

The ETH withdrawal lifecycle in `LRTWithdrawalManager` has three stages:

**Stage 1 — `initiateWithdrawal`:** The user transfers rsETH to the contract. A `WithdrawalRequest` is stored in LOCKED state (nonce < `nextLockedNonce[asset]` is false). [1](#0-0) 

**Stage 2 — `unlockQueue` (operator-only):** rsETH is burned and ETH is redeemed from `LRTUnstakingVault`. When Aave integration is enabled, the redeemed ETH is immediately deposited into Aave v3 via `depositToAaveExternal`. [2](#0-1) 

**Stage 3 — `completeWithdrawal`:** `_processWithdrawalCompletion` attempts to withdraw the user's ETH from Aave. If the contract's ETH balance is insufficient, it calls `_withdrawFromAave`. If Aave's WETH pool has insufficient liquidity, the function reverts with `InsufficientLiquidityForWithdrawal`. [3](#0-2) 

The `_withdrawFromAave` function caps the withdrawal at `withdrawablePrincipal` (the lesser of `aaveBalance` and `totalETHDepositedToAave`). If this is less than `request.expectedAssetAmount`, the balance check at line 728 fails and the entire transaction reverts. [4](#0-3) 

**The critical gap:** There is no `cancelWithdrawal` function anywhere in `LRTWithdrawalManager`. Once a request is in the unlocked state, the user has no path to recover their position. Their rsETH was already burned in Stage 2 and cannot be reminted. The `WithdrawalRequest` struct has no state field that would allow a cancel path. [5](#0-4) 

---

### Impact Explanation

**Temporary freezing of funds (Medium).** The user's ETH is locked inside Aave and the corresponding rsETH has been permanently burned. The user cannot exit the position, cannot cancel the request, and cannot receive any asset until Aave's WETH pool regains sufficient liquidity. This is a direct analog to the reported pattern: a two-step process where the first step is irreversible (rsETH burn) and the second step can fail (Aave liquidity), with no cancel path.

---

### Likelihood Explanation

**Medium.** The Aave v3 WETH pool can experience liquidity crunches during high-utilization periods (e.g., market stress, large borrowing demand). The Kelp protocol may hold significant ETH in Aave on behalf of many users, and a single high-utilization event would block all pending ETH withdrawals simultaneously. The Aave integration is an opt-in feature controlled by the manager, so it is an active, production-intended code path.

---

### Recommendation

Add a user-callable `cancelUnlockedWithdrawal` function that:
1. Verifies the request is in the unlocked state and the caller is the original withdrawer.
2. Withdraws the ETH from Aave (or uses the contract's idle ETH balance).
3. Returns the ETH directly to the user (since rsETH is already burned, the user receives the underlying ETH equivalent).

Alternatively, implement a fallback in `_processWithdrawalCompletion` that, when Aave liquidity is insufficient, marks the request as "pending retry" rather than reverting, so the user's nonce is not re-queued in an inconsistent state.

---

### Proof of Concept

1. Aave integration is enabled (`isAaveIntegrationEnabled == true`).
2. User calls `initiateWithdrawal(ETH_TOKEN, 1 ether rsETH, "")` → rsETH transferred to `LRTWithdrawalManager`.
3. Operator calls `unlockQueue(ETH_TOKEN, ...)`:
   - rsETH is burned: `IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned)` [6](#0-5) 
   - ETH redeemed from vault: `unstakingVault.redeem(asset, assetAmountUnlocked)` [7](#0-6) 
   - ETH deposited to Aave: `this.depositToAaveExternal(assetAmountUnlocked)` [8](#0-7) 
4. Aave WETH pool utilization reaches 100% (all WETH borrowed out).
5. User calls `completeWithdrawal(ETH_TOKEN, "")`:
   - `contractBalance < request.expectedAssetAmount` → calls `_withdrawFromAave(amountNeeded)` [9](#0-8) 
   - `aaveWETHGateway.withdrawETH(...)` reverts due to insufficient Aave liquidity, or `balanceAfter < request.expectedAssetAmount` → `revert InsufficientLiquidityForWithdrawal()` [10](#0-9) 
6. User's rsETH is gone. No cancel function exists. ETH is frozen in Aave until liquidity is restored.

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

**File:** contracts/LRTWithdrawalManager.sol (L305-317)
```text
        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);

        // If Aave integration is enabled and asset is ETH, deposit to Aave
        if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN && assetAmountUnlocked > 0) {
            try this.depositToAaveExternal(assetAmountUnlocked) { }
            catch (bytes memory reason) {
                emit AaveDepositFailed(assetAmountUnlocked, reason);
                // Silently fail if Aave deposit fails (e.g., pool at max capacity)
                // Funds remain in contract for withdrawals
            }
        }
```

**File:** contracts/LRTWithdrawalManager.sol (L720-731)
```text
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
```

**File:** contracts/LRTWithdrawalManager.sol (L905-921)
```text
    function _withdrawFromAave(uint256 amount) internal returns (uint256 withdrawnAmount) {
        if (amount == 0) return 0;

        uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
        if (aaveBalance == 0) revert InsufficientAaveBalance();

        // Only withdraw up to the principal amount (don't use accrued interest for user withdrawals)
        uint256 withdrawablePrincipal = aaveBalance < totalETHDepositedToAave ? aaveBalance : totalETHDepositedToAave;

        withdrawnAmount = amount > withdrawablePrincipal ? withdrawablePrincipal : amount;
        if (withdrawnAmount == 0) return 0;

        aaveWETHGateway.withdrawETH(aavePool, withdrawnAmount, address(this));
        totalETHDepositedToAave -= withdrawnAmount;

        emit ETHWithdrawnFromAave(withdrawnAmount, totalETHDepositedToAave);
    }
```

**File:** contracts/interfaces/ILRTWithdrawalManager.sol (L39-43)
```text
    struct WithdrawalRequest {
        uint256 rsETHUnstaked;
        uint256 expectedAssetAmount;
        uint256 withdrawalStartBlock;
    }
```
