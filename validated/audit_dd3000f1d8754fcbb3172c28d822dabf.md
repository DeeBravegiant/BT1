I have all the code needed. Let me analyze the exact logic carefully.

**Key code paths:**

`getAaveWithdrawableLiquidity()` (line 684–689): [1](#0-0) 

Returns `IERC20(WETH_ADDRESS).balanceOf(address(aaveAWETH))` — the pool-level WETH liquidity, completely unrelated to the contract's own principal or `totalETHDepositedToAave`.

`_withdrawFromAave()` (line 905–921): [2](#0-1) 

When `totalETHDepositedToAave == 0`:
- `aaveBalance > 0` → does NOT revert at line 909
- `withdrawablePrincipal = min(aaveBalance, 0) = 0`
- `withdrawnAmount = 0` → **silently returns 0**

`_processWithdrawalCompletion()` (line 720–732): [3](#0-2) 

Calls `_withdrawFromAave(amountNeeded)`, gets 0 back, then reverts with `InsufficientLiquidityForWithdrawal`.

`_checkAaveHealth()` (line 925–933): [4](#0-3) 

When `totalETHDepositedToAave == 0` and `aaveBalance == 0.5 ETH`: `principal (0) > aaveBalance (0.5)` is false → returns **true** (healthy). Misleading.

**Is the precondition `totalETHDepositedToAave == 0` with `aaveAWETH.balanceOf > 0` reachable?**

Yes, in normal operation:
1. 100 ETH deposited → `totalETHDepositedToAave = 100`, `aaveBalance = 100`
2. Interest accrues → `aaveBalance = 100.5`
3. `_withdrawFromAave(100)` called for user withdrawals: `withdrawablePrincipal = min(100.5, 100) = 100`, withdraws 100, sets `totalETHDepositedToAave = 0`, but `aaveBalance = 0.5` remains (interest residual)

**`emergencyWithdrawFromAave` is also broken in this state:** [5](#0-4) 

It calls `_collectInterestToTreasury()` first (drains the 0.5 ETH residual to treasury), then calls `_withdrawFromAave(amount)` with `aaveBalance == 0` → **reverts with `InsufficientAaveBalance`**. The emergency function itself fails.

**Recovery path exists (making this temporary, not permanent):**

The operator can call `collectInterestToTreasury()` directly (which succeeds — `_checkAaveHealth()` returns true, and `_collectInterestToTreasury` correctly identifies `aaveBalance > principal`), then call `setAaveIntegrationEnabled(false)`. After disabling Aave, user withdrawals skip the Aave block and can complete once the contract has sufficient ETH from the unstaking vault.

---

### Title
Misleading `getAaveWithdrawableLiquidity` and Silent `_withdrawFromAave` Return When `totalETHDepositedToAave == 0` Causes Temporary ETH Withdrawal Freeze — (`contracts/LRTWithdrawalManager.sol`)

### Summary
When `totalETHDepositedToAave == 0` but `aaveAWETH.balanceOf(address(this)) > 0` (interest residual), `_withdrawFromAave` silently returns 0, causing `_processWithdrawalCompletion` to revert. Simultaneously, `getAaveWithdrawableLiquidity`, `getAaveBalance`, and `aaveHealthCheck` all return positive/healthy values, masking the broken state. `emergencyWithdrawFromAave` also reverts in this state due to the `_collectInterestToTreasury` + `_withdrawFromAave` sequencing bug.

### Finding Description
`_withdrawFromAave` computes `withdrawablePrincipal = min(aaveBalance, totalETHDepositedToAave)`. When `totalETHDepositedToAave == 0`, this is always 0 regardless of `aaveBalance`, and the function returns 0 silently (line 915). This state is reachable in normal operation: as interest accrues between the last `collectInterestToTreasury` call and the final principal withdrawal, `totalETHDepositedToAave` reaches 0 while a small aWETH residual remains.

In this state:
- `getAaveWithdrawableLiquidity()` returns the Aave pool's total WETH liquidity (unrelated to the contract's position) — always large and positive
- `getAaveBalance()` returns the interest residual — positive
- `aaveHealthCheck()` returns `true` — since `principal (0) <= aaveBalance`
- `emergencyWithdrawFromAave()` reverts — `_collectInterestToTreasury` drains the residual to treasury, then `_withdrawFromAave` hits `aaveBalance == 0` and reverts with `InsufficientAaveBalance`

### Impact Explanation
**Medium — Temporary freezing of funds.** User ETH withdrawal requests revert with `InsufficientLiquidityForWithdrawal`. Since the revert undoes all state changes (including the `delete withdrawalRequests[requestId]` at line 712), the user's request remains in the queue and is not lost. Recovery requires the operator to call `collectInterestToTreasury()` directly, then `setAaveIntegrationEnabled(false)`, after which withdrawals proceed normally once the contract has sufficient ETH. The freeze is not permanent but can persist until an operator diagnoses and resolves the state — delayed by the misleading view functions.

### Likelihood Explanation
Medium. This state arises in normal operation whenever interest accrues between the last interest collection and the final principal withdrawal. No adversarial action is required. Any protocol with active Aave integration and ongoing user withdrawals will eventually reach this state.

### Recommendation
1. In `_withdrawFromAave`, when `totalETHDepositedToAave == 0` and `aaveBalance > 0`, either revert with a descriptive error or treat the residual as withdrawable (after collecting it as interest).
2. Fix `emergencyWithdrawFromAave` to not call `_withdrawFromAave` after `_collectInterestToTreasury` has already drained the balance — or re-read `aaveBalance` after interest collection and skip `_withdrawFromAave` if it is now 0.
3. Fix `getAaveWithdrawableLiquidity` to return `min(WETH_balance_of_aWETH, totalETHDepositedToAave)` so it reflects the contract's actual withdrawable principal, not pool-level liquidity.
4. Fix `aaveHealthCheck` to return `false` when `totalETHDepositedToAave == 0` and `aaveBalance > 0` (orphaned interest residual state).

### Proof of Concept
```solidity
// State: totalETHDepositedToAave = 0, aaveAWETH.balanceOf(contract) = 0.5 ETH (interest residual)
// isAaveIntegrationEnabled = true

// Step 1: View functions show "healthy"
assert(withdrawalManager.getAaveWithdrawableLiquidity() > 0);  // pool WETH balance
assert(withdrawalManager.getAaveBalance() > 0);                // 0.5 ETH residual
assert(withdrawalManager.aaveHealthCheck() == true);           // principal(0) <= balance(0.5)

// Step 2: User tries to complete withdrawal (contract has 0 ETH, needs 1 ETH from Aave)
// _withdrawFromAave(1 ETH):
//   aaveBalance = 0.5 > 0 → no revert
//   withdrawablePrincipal = min(0.5, 0) = 0
//   withdrawnAmount = 0 → returns 0 silently
// balanceAfter (0) < expectedAssetAmount (1 ETH) → revert InsufficientLiquidityForWithdrawal

// Step 3: emergencyWithdrawFromAave also fails
// _collectInterestToTreasury() → withdraws 0.5 ETH to treasury, aaveBalance = 0
// _withdrawFromAave(amount) → aaveBalance == 0 → revert InsufficientAaveBalance
```

### Citations

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

**File:** contracts/LRTWithdrawalManager.sol (L684-689)
```text
    function getAaveWithdrawableLiquidity() external view returns (uint256 withdrawableLiquidity) {
        if (address(aaveAWETH) == address(0)) return 0;

        // The withdrawable liquidity is the WETH balance of the aWETH contract
        return IERC20(WETH_ADDRESS).balanceOf(address(aaveAWETH));
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L720-732)
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
