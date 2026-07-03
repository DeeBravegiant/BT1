### Title
`emergencyWithdrawFromAave` Does Not Disable Aave Integration, Causing Temporary Freeze of User ETH Withdrawals - (File: contracts/LRTWithdrawalManager.sol)

---

### Summary

`emergencyWithdrawFromAave` drains ETH from Aave back to the contract but never sets `isAaveIntegrationEnabled = false`. The contract is left in an inconsistent state: it believes Aave is active, but the Aave balance is zero. Subsequent calls to `completeWithdrawal` for ETH will attempt to pull from the empty Aave pool and revert with `InsufficientAaveBalance`, temporarily freezing user ETH withdrawals.

---

### Finding Description

`emergencyWithdrawFromAave` is the direct analog of the Paladin emergency-withdraw bug: a special-path function that moves funds but fails to reset a critical state variable, leaving the system in an inconsistent state that breaks normal operation once the emergency is over.

The function:

```solidity
function emergencyWithdrawFromAave(uint256 amount) external nonReentrant onlyRole(LRTConstants.PAUSER_ROLE) {
    if (!isAaveIntegrationEnabled) revert AaveIntegrationNotEnabled();
    uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
    if (aaveBalance == 0) revert InsufficientAaveBalance();
    _collectInterestToTreasury();
    uint256 withdrawnAmount = _withdrawFromAave(amount);
    emit EmergencyWithdrawFromAave(withdrawnAmount, address(this));
}
``` [1](#0-0) 

After this call, `_withdrawFromAave` correctly decrements `totalETHDepositedToAave` to zero, but `isAaveIntegrationEnabled` is never set to `false`. [2](#0-1) 

The stale `isAaveIntegrationEnabled = true` flag is then read inside `_processWithdrawalCompletion`:

```solidity
if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN) {
    uint256 contractBalance = address(this).balance;
    if (contractBalance < request.expectedAssetAmount) {
        uint256 amountNeeded = request.expectedAssetAmount - contractBalance;
        _withdrawFromAave(amountNeeded);   // ← reverts: aaveBalance == 0
        ...
    }
}
``` [3](#0-2) 

`_withdrawFromAave` unconditionally reverts when `aaveBalance == 0`:

```solidity
uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
if (aaveBalance == 0) revert InsufficientAaveBalance();
``` [4](#0-3) 

This revert propagates through `completeWithdrawal` / `completeWithdrawalForUser`, blocking all ETH withdrawal completions once the contract's native ETH balance is exhausted. [5](#0-4) 

By contrast, the proper `setAaveIntegrationEnabled(false)` path does set the flag and revokes approvals:

```solidity
isAaveIntegrationEnabled = enabled;
``` [6](#0-5) 

`emergencyWithdrawFromAave` skips this entirely.

---

### Impact Explanation

**Temporary freezing of user ETH withdrawal funds.**

After the emergency withdrawal, the ETH returned to the contract is finite. Once it is consumed by the first batch of `completeWithdrawal` calls, every subsequent ETH withdrawal reverts with `InsufficientAaveBalance`. Users with unlocked, delay-passed ETH withdrawal requests cannot claim their funds until an admin separately calls `setAaveIntegrationEnabled(false)`. The rsETH they burned at `initiateWithdrawal` time is already gone; they are stuck waiting. [7](#0-6) 

---

### Likelihood Explanation

`emergencyWithdrawFromAave` is explicitly designed to be called by `PAUSER_ROLE` in crisis situations (Aave exploit, liquidity crunch, etc.). It is a realistic, intended operational path. No attacker action is required — the bug manifests automatically for every subsequent ETH `completeWithdrawal` call once the contract's native balance is insufficient. The only recovery is a separate admin transaction to disable Aave integration.

---

### Recommendation

Set `isAaveIntegrationEnabled = false` and revoke the Aave approval inside `emergencyWithdrawFromAave`, mirroring what `setAaveIntegrationEnabled(false)` already does:

```solidity
function emergencyWithdrawFromAave(uint256 amount) external nonReentrant onlyRole(LRTConstants.PAUSER_ROLE) {
    if (!isAaveIntegrationEnabled) revert AaveIntegrationNotEnabled();
    uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
    if (aaveBalance == 0) revert InsufficientAaveBalance();
    _collectInterestToTreasury();
    uint256 withdrawnAmount = _withdrawFromAave(amount);
+   isAaveIntegrationEnabled = false;          // reset state
+   _revokeApprovalToAaveWETHGateway();        // revoke approval
+   emit AaveIntegrationEnabled(false);
    emit EmergencyWithdrawFromAave(withdrawnAmount, address(this));
}
```

---

### Proof of Concept

1. Aave integration is enabled; operator calls `unlockQueue` for ETH → `depositToAaveExternal` deposits 100 ETH to Aave; `totalETHDepositedToAave = 100 ETH`, `isAaveIntegrationEnabled = true`.
2. PAUSER calls `emergencyWithdrawFromAave(type(uint256).max)` → 100 ETH returns to contract, `totalETHDepositedToAave = 0`, **`isAaveIntegrationEnabled` remains `true`**.
3. User A calls `completeWithdrawal(ETH_TOKEN, ...)` for 60 ETH → `contractBalance (100) >= 60` → succeeds; contract now holds 40 ETH.
4. User B calls `completeWithdrawal(ETH_TOKEN, ...)` for 50 ETH → `contractBalance (40) < 50` → enters Aave branch → calls `_withdrawFromAave(10 ETH)` → `aaveAWETH.balanceOf(address(this)) == 0` → **`revert InsufficientAaveBalance()`**.
5. User B's withdrawal is frozen. Every subsequent ETH withdrawal is also frozen until an admin manually calls `setAaveIntegrationEnabled(false)`. [8](#0-7)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-173)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;
```

**File:** contracts/LRTWithdrawalManager.sol (L183-185)
```text
    function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
        _processWithdrawalCompletion(asset, msg.sender, referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L503-504)
```text
        isAaveIntegrationEnabled = enabled;
        emit AaveIntegrationEnabled(enabled);
```

**File:** contracts/LRTWithdrawalManager.sol (L551-563)
```text
    function emergencyWithdrawFromAave(uint256 amount) external nonReentrant onlyRole(LRTConstants.PAUSER_ROLE) {
        if (!isAaveIntegrationEnabled) revert AaveIntegrationNotEnabled();

        uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
        if (aaveBalance == 0) revert InsufficientAaveBalance();

        // First collect any accrued interest to treasury
        _collectInterestToTreasury();

        uint256 withdrawnAmount = _withdrawFromAave(amount);

        emit EmergencyWithdrawFromAave(withdrawnAmount, address(this));
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L699-738)
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

        emit ReferralIdEmitted(referralId);
        emit AssetWithdrawalFinalized(user, asset, request.rsETHUnstaked, request.expectedAssetAmount);
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
