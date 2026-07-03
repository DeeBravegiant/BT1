### Title
Unhandled Aave Withdrawal Failure in `_processWithdrawalCompletion` Causes Temporary Freezing of User ETH Withdrawals - (File: contracts/LRTWithdrawalManager.sol)

---

### Summary

When the Aave integration is enabled, `LRTWithdrawalManager._processWithdrawalCompletion` calls `_withdrawFromAave` without any try/catch error handling. If Aave's `withdrawETH` reverts (e.g., Aave is paused, liquidity is insufficient), the entire `completeWithdrawal` call reverts. Because the user's rsETH has already been burned in a prior `unlockQueue` call, the user's ETH is temporarily (or permanently) frozen in Aave and cannot be retrieved. This is the direct Solidity analog to M-07: an external call that can fail is made without graceful error handling, causing unexpected reverts that propagate up and freeze user funds.

---

### Finding Description

`_processWithdrawalCompletion` is the internal function executed by both the user-callable `completeWithdrawal` and the operator-callable `completeWithdrawalForUser`. When the Aave integration is enabled and the contract's native ETH balance is insufficient to cover a user's withdrawal, it calls `_withdrawFromAave` to pull funds from Aave: [1](#0-0) 

```solidity
if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN) {
    uint256 contractBalance = address(this).balance;
    if (contractBalance < request.expectedAssetAmount) {
        uint256 amountNeeded = request.expectedAssetAmount - contractBalance;
        _withdrawFromAave(amountNeeded);   // ← no try/catch
        uint256 balanceAfter = address(this).balance;
        if (balanceAfter < request.expectedAssetAmount) {
            revert InsufficientLiquidityForWithdrawal();
        }
    }
}
```

`_withdrawFromAave` makes a direct, unguarded external call to Aave: [2](#0-1) 

```solidity
aaveWETHGateway.withdrawETH(aavePool, withdrawnAmount, address(this));
```

If `aaveWETHGateway.withdrawETH` reverts for any reason (Aave paused, pool at capacity, emergency shutdown), the revert propagates all the way up through `_processWithdrawalCompletion` and causes `completeWithdrawal` to revert entirely.

This is in direct contrast to how `unlockQueue` handles Aave **deposits** — with an explicit try/catch that silently absorbs failures: [3](#0-2) 

```solidity
try this.depositToAaveExternal(assetAmountUnlocked) { }
catch (bytes memory reason) {
    emit AaveDepositFailed(assetAmountUnlocked, reason);
    // Silently fail if Aave deposit fails
}
```

The asymmetry is the root cause: Aave deposit failures are handled gracefully; Aave withdrawal failures are not.

The withdrawal lifecycle that leads to fund freeze:

1. `initiateWithdrawal` — user's rsETH is transferred to the withdrawal manager.
2. `unlockQueue` — rsETH is **burned** (`IRSETH.burnFrom`) and ETH is deposited into Aave via `_depositToAave`.
3. `completeWithdrawal` — if Aave is unavailable, `_withdrawFromAave` reverts, the entire call reverts, and the user's ETH remains locked in Aave with no rsETH to reclaim. [4](#0-3) 

---

### Impact Explanation

**Temporary (or permanent) freezing of user ETH funds.** After `unlockQueue` burns the user's rsETH and deposits ETH into Aave, any Aave-side revert during `completeWithdrawal` leaves the user with:
- No rsETH (already burned).
- No ETH (locked in Aave).
- No ability to retry successfully until Aave becomes available.

If Aave is permanently deprecated or suffers an unrecoverable failure, the freeze becomes permanent. This matches the **Medium — Temporary freezing of funds** impact category (and potentially Critical — Permanent freezing of funds in the worst case).

---

### Likelihood Explanation

Aave v3 has a well-documented pause mechanism exercisable by Aave governance and emergency admins. Any Aave pause during the window between `unlockQueue` and `completeWithdrawal` would affect all users with pending ETH withdrawals backed by Aave liquidity. This is a realistic, non-hypothetical scenario given Aave's operational history.

---

### Recommendation

Wrap the `_withdrawFromAave` call inside `_processWithdrawalCompletion` with a try/catch, consistent with how `unlockQueue` handles Aave deposit failures. If the Aave withdrawal fails, revert with a descriptive error (e.g., `AaveWithdrawalFailed`) so the user can retry later, rather than silently propagating the Aave revert:

```solidity
try this.withdrawFromAaveExternal(amountNeeded) {
    // success
} catch (bytes memory reason) {
    revert AaveWithdrawalFailed(reason);
}
```

Alternatively, expose a separate `emergencyWithdrawFromAave` path (already exists for the pauser role) that operators can use to drain Aave funds back to the contract before users attempt `completeWithdrawal`.

---

### Proof of Concept

1. User (EOA or contract) calls `initiateWithdrawal(ETH_TOKEN, rsETHAmount, "")` — rsETH is transferred to `LRTWithdrawalManager`.
2. Operator calls `unlockQueue(ETH_TOKEN, ...)` — rsETH is burned; ETH is deposited into Aave via `_depositToAave`.
3. Aave governance pauses the Aave v3 pool (a known, exercised capability).
4. User calls `completeWithdrawal(ETH_TOKEN, "")`:
   - `_processWithdrawalCompletion` is entered.
   - `contractBalance < request.expectedAssetAmount` (ETH is in Aave, not in the contract).
   - `_withdrawFromAave(amountNeeded)` is called.
   - `aaveWETHGateway.withdrawETH(...)` reverts because Aave is paused.
   - The revert propagates; the entire transaction reverts.
5. User's rsETH is already burned. ETH is locked in Aave. User cannot withdraw. [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L183-185)
```text
    function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
        _processWithdrawalCompletion(asset, msg.sender, referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L305-307)
```text
        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTWithdrawalManager.sol (L310-317)
```text
        if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN && assetAmountUnlocked > 0) {
            try this.depositToAaveExternal(assetAmountUnlocked) { }
            catch (bytes memory reason) {
                emit AaveDepositFailed(assetAmountUnlocked, reason);
                // Silently fail if Aave deposit fails (e.g., pool at max capacity)
                // Funds remain in contract for withdrawals
            }
        }
```

**File:** contracts/LRTWithdrawalManager.sol (L719-731)
```text
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
```

**File:** contracts/LRTWithdrawalManager.sol (L905-918)
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
```
