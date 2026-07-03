### Title
`emergencyWithdrawFromAave` Does Not Disable Aave Integration, Leaving `LRTWithdrawalManager` in a Broken State - (File: contracts/LRTWithdrawalManager.sol)

---

### Summary

`emergencyWithdrawFromAave` withdraws ETH from Aave but does not set `isAaveIntegrationEnabled = false`. After the emergency drains Aave, `_processWithdrawalCompletion` (called by every `completeWithdrawal`) still branches into the Aave-withdrawal path and calls `_withdrawFromAave`, which hard-reverts when `aaveBalance == 0`. This permanently blocks any ETH withdrawal request that cannot be fully covered by the contract's idle ETH balance, freezing user funds until a separate admin action is taken.

---

### Finding Description

`emergencyWithdrawFromAave` is callable by `PAUSER_ROLE` and is intended for emergency situations (e.g., Aave is compromised):

```solidity
// contracts/LRTWithdrawalManager.sol:551-563
function emergencyWithdrawFromAave(uint256 amount) external nonReentrant onlyRole(LRTConstants.PAUSER_ROLE) {
    if (!isAaveIntegrationEnabled) revert AaveIntegrationNotEnabled();
    uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
    if (aaveBalance == 0) revert InsufficientAaveBalance();
    _collectInterestToTreasury();
    uint256 withdrawnAmount = _withdrawFromAave(amount);
    emit EmergencyWithdrawFromAave(withdrawnAmount, address(this));
}
```

After this call, `isAaveIntegrationEnabled` remains `true`. [1](#0-0) 

Every ETH `completeWithdrawal` flows through `_processWithdrawalCompletion`, which checks `isAaveIntegrationEnabled` and calls `_withdrawFromAave` whenever the contract's idle ETH balance is insufficient:

```solidity
// contracts/LRTWithdrawalManager.sol:720-731
if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN) {
    uint256 contractBalance = address(this).balance;
    if (contractBalance < request.expectedAssetAmount) {
        uint256 amountNeeded = request.expectedAssetAmount - contractBalance;
        _withdrawFromAave(amountNeeded);          // <-- no try/catch
        uint256 balanceAfter = address(this).balance;
        if (balanceAfter < request.expectedAssetAmount) {
            revert InsufficientLiquidityForWithdrawal();
        }
    }
}
``` [2](#0-1) 

`_withdrawFromAave` hard-reverts when `aaveBalance == 0`:

```solidity
// contracts/LRTWithdrawalManager.sol:905-909
function _withdrawFromAave(uint256 amount) internal returns (uint256 withdrawnAmount) {
    if (amount == 0) return 0;
    uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
    if (aaveBalance == 0) revert InsufficientAaveBalance();
    ...
``` [3](#0-2) 

Contrast this with `unlockQueue`, which wraps its Aave deposit in `try/catch` and silently continues — the withdrawal completion path has no such safety net. [4](#0-3) 

---

### Impact Explanation

**Temporary freezing of user ETH withdrawal funds.**

Concrete scenario:
1. 100 ETH is deposited in Aave; `isAaveIntegrationEnabled = true`.
2. Two users have unlocked withdrawal requests: User A for 60 ETH, User B for 60 ETH.
3. Pauser calls `emergencyWithdrawFromAave(100 ETH)` → 100 ETH lands in the contract, Aave is drained, but `isAaveIntegrationEnabled` stays `true`.
4. User A calls `completeWithdrawal` → `contractBalance (100) >= 60` → succeeds; contract balance drops to 40 ETH.
5. User B calls `completeWithdrawal` → `contractBalance (40) < 60` → `_withdrawFromAave(20)` is called → `aaveBalance == 0` → **reverts with `InsufficientAaveBalance`**.
6. User B's withdrawal is frozen until a manager separately calls `setAaveIntegrationEnabled(false)`.

The `_checkAaveHealth` function does not catch this broken state: after a full emergency withdrawal both `totalETHDepositedToAave` and `aaveBalance` are 0, so `principal > aaveBalance` is `false` and the function incorrectly returns `true`. [5](#0-4) 

---

### Likelihood Explanation

**Low.** Requires: (a) an emergency event that prompts a pauser to call `emergencyWithdrawFromAave`, and (b) the contract's idle ETH balance being insufficient to cover at least one pending unlocked withdrawal after the emergency. Both conditions are plausible in a real emergency (e.g., Aave exploit) but are not the normal operating path.

---

### Recommendation

`emergencyWithdrawFromAave` should set `isAaveIntegrationEnabled = false` after withdrawing, mirroring the cleanup logic already present in `setAaveIntegrationEnabled(false)`:

```solidity
function emergencyWithdrawFromAave(uint256 amount) external nonReentrant onlyRole(LRTConstants.PAUSER_ROLE) {
    if (!isAaveIntegrationEnabled) revert AaveIntegrationNotEnabled();
    uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
    if (aaveBalance == 0) revert InsufficientAaveBalance();
    _collectInterestToTreasury();
    uint256 withdrawnAmount = _withdrawFromAave(amount);
+   isAaveIntegrationEnabled = false;          // prevent broken-state re-entry
+   _revokeApprovalToAaveWETHGateway();
    emit EmergencyWithdrawFromAave(withdrawnAmount, address(this));
+   emit AaveIntegrationEnabled(false);
}
```

Alternatively, `_processWithdrawalCompletion` should guard the `_withdrawFromAave` call with a balance check and gracefully skip it (or revert with a clearer error) when Aave is empty.

---

### Proof of Concept

1. Aave integration is active; 100 ETH is deposited (`totalETHDepositedToAave = 100`).
2. `unlockQueue` unlocks two requests: 60 ETH for Alice, 60 ETH for Bob. Both are now claimable.
3. Pauser calls `emergencyWithdrawFromAave(type(uint256).max)`.
   - `_withdrawFromAave` withdraws 100 ETH → `totalETHDepositedToAave = 0`, `aaveBalance = 0`.
   - `isAaveIntegrationEnabled` **remains `true`**.
   - Contract ETH balance = 100 ETH.
4. Alice calls `completeWithdrawal(ETH)` → `100 >= 60` → succeeds. Contract balance = 40 ETH.
5. Bob calls `completeWithdrawal(ETH)`:
   - `isAaveIntegrationEnabled == true` → enters Aave branch.
   - `40 < 60` → calls `_withdrawFromAave(20)`.
   - `aaveAWETH.balanceOf(address(this)) == 0` → **`revert InsufficientAaveBalance()`**.
6. Bob's 60 ETH is frozen. No user-callable path can unblock it; only a manager calling `setAaveIntegrationEnabled(false)` restores normal operation. [1](#0-0) [6](#0-5) [7](#0-6)

### Citations

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

**File:** contracts/LRTWithdrawalManager.sol (L719-732)
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

**File:** contracts/LRTWithdrawalManager.sol (L925-933)
```text
    function _checkAaveHealth() internal view returns (bool healthy) {
        if (!isAaveIntegrationEnabled) return false;
        uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
        uint256 principal = totalETHDepositedToAave;
        // Allow small rounding differences (up to 2 wei)
        // Check if balance is significantly less than principal
        if (principal > aaveBalance && principal - aaveBalance > 2) return false;
        return true;
    }
```
