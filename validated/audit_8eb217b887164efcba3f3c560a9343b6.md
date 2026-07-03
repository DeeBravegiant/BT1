### Title
Aave Pool Pause Permanently Blocks ETH Withdrawals in `completeWithdrawal` - (File: contracts/LRTWithdrawalManager.sol)

### Summary

When the Aave integration is enabled, `LRTWithdrawalManager.completeWithdrawal()` unconditionally calls `aaveWETHGateway.withdrawETH()` to source ETH for user withdrawals. If Aave's WETH reserve is paused (a standard Aave v3 guardian action), this call reverts with no fallback, permanently blocking all ETH withdrawal completions for users whose rsETH has already been burned.

### Finding Description

`LRTWithdrawalManager` integrates with Aave v3 to earn yield on idle ETH held for pending withdrawals. When `isAaveIntegrationEnabled` is true and the asset is ETH, `_processWithdrawalCompletion` checks whether the contract's native ETH balance is sufficient to cover the withdrawal. If not, it calls `_withdrawFromAave`: [1](#0-0) 

`_withdrawFromAave` calls `aaveWETHGateway.withdrawETH(aavePool, withdrawnAmount, address(this))` with no try/catch: [2](#0-1) 

Aave v3 has a guardian role that can pause individual reserves via `setReservePause`. When the WETH reserve is paused, any call to `withdrawETH` reverts. Since `_processWithdrawalCompletion` has no fallback path and no try/catch around this call, the entire `completeWithdrawal` transaction reverts.

The user-facing entry point is: [3](#0-2) 

The supposed admin escape hatch, `emergencyWithdrawFromAave`, also calls `_withdrawFromAave` internally: [4](#0-3) 

This means even the PAUSER_ROLE cannot rescue funds from Aave while the reserve is paused, leaving no recovery path.

### Impact Explanation

Users who have initiated withdrawals (rsETH burned by the operator in `unlockQueue`, withdrawal request unlocked) cannot complete their ETH withdrawals for the entire duration of the Aave pause. Their rsETH is already gone and their ETH is locked in Aave. This constitutes a **temporary freezing of funds** (Medium severity). If the Aave pause is indefinite or the reserve is deprecated, it escalates to permanent freezing (Critical).

### Likelihood Explanation

Aave v3 guardian pauses are a documented, exercised mechanism used during security incidents (e.g., the November 2023 Aave CRV incident). The WETH reserve on mainnet is a high-value target. The Aave integration is explicitly enabled by the LRT manager, making this a live production risk whenever `isAaveIntegrationEnabled == true` and ETH withdrawals are pending.

### Recommendation

Wrap the `_withdrawFromAave` call in `_processWithdrawalCompletion` in a try/catch. If the Aave withdrawal fails, revert with a descriptive error (e.g., `AaveWithdrawalFailed`) so the user can retry later, but also add a separate admin function that can withdraw from Aave using a direct `aavePool.withdraw()` call (bypassing the gateway) or that can disable the Aave integration even when Aave is paused, allowing the protocol to source ETH from other means. Additionally, `emergencyWithdrawFromAave` should not rely on `_withdrawFromAave` — it should have a direct low-level path that works even when the gateway is paused.

### Proof of Concept

1. Operator calls `unlockQueue(ETH_TOKEN, ...)` — rsETH is burned, user's withdrawal is unlocked, ETH is deposited to Aave via `depositToAaveExternal`.
2. Aave guardian pauses the WETH reserve (`setReservePause(WETH, true)`).
3. User calls `completeWithdrawal(ETH_TOKEN, referralId)`.
4. `_processWithdrawalCompletion` checks `address(this).balance < request.expectedAssetAmount` → true (ETH is in Aave).
5. `_withdrawFromAave(amountNeeded)` is called → `aaveWETHGateway.withdrawETH(aavePool, withdrawnAmount, address(this))` reverts because the reserve is paused.
6. Entire transaction reverts. User cannot receive ETH.
7. `emergencyWithdrawFromAave` also reverts for the same reason — no admin recovery path exists.
8. User's rsETH is permanently burned; ETH is frozen in Aave until the reserve is unpaused. [1](#0-0) [5](#0-4)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L183-185)
```text
    function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
        _processWithdrawalCompletion(asset, msg.sender, referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L551-562)
```text
    function emergencyWithdrawFromAave(uint256 amount) external nonReentrant onlyRole(LRTConstants.PAUSER_ROLE) {
        if (!isAaveIntegrationEnabled) revert AaveIntegrationNotEnabled();

        uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
        if (aaveBalance == 0) revert InsufficientAaveBalance();

        // First collect any accrued interest to treasury
        _collectInterestToTreasury();

        uint256 withdrawnAmount = _withdrawFromAave(amount);

        emit EmergencyWithdrawFromAave(withdrawnAmount, address(this));
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
