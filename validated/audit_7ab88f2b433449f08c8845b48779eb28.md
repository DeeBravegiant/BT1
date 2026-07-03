The code is fully readable. Let me trace the exact execution path.

**`_withdrawFromAave` when `totalETHDepositedToAave = 0`:**

```
withdrawablePrincipal = min(aaveBalance, 0) = 0
withdrawnAmount       = min(amountNeeded, 0) = 0
→ returns 0 (line 915: "if (withdrawnAmount == 0) return 0")
```

**`_processWithdrawalCompletion` consequence:**

```
_withdrawFromAave(amountNeeded) → 0 ETH withdrawn
balanceAfter == contractBalance (unchanged)
balanceAfter < request.expectedAssetAmount → revert InsufficientLiquidityForWithdrawal
```

**Reachability of `totalETHDepositedToAave = 0` with `aaveBalance > 0`:**

This is a normal operational state:
1. 100 ETH deposited → `totalETHDepositedToAave = 100`, `aaveBalance = 100`
2. Interest accrues → `aaveBalance = 105`
3. All principal withdrawn via `_withdrawFromAave(100)` → `totalETHDepositedToAave = 0`, `aaveBalance = 5`

**Mitigation paths analysis:**

- `emergencyWithdrawFromAave`: calls `_collectInterestToTreasury()` (sends 5 ETH to treasury), then `_withdrawFromAave(amount)` which still returns 0 because `totalETHDepositedToAave = 0`. Does not help.
- `setAaveIntegrationEnabled(false)`: calls `_collectInterestToTreasury()` (interest → treasury), then `_withdrawFromAave(aaveBalance)` → returns 0. Sets `isAaveIntegrationEnabled = false`. After this, `completeWithdrawal` skips the Aave block but the contract still has no ETH.
- No function redirects interest ETH to the contract for user withdrawals.

**User fund loss path:**

rsETH is burned in `unlockQueue` before `completeWithdrawal` is called. After burning, the user has no rsETH and their `completeWithdrawal` permanently reverts. The only recovery is admin manually sending ETH to the contract (treasury → contract), which is not a protocol-guaranteed path.

---

### Title
Principal-Only Cap in `_withdrawFromAave` Permanently Blocks ETH Withdrawals When `totalETHDepositedToAave = 0` and Aave Interest Remains - (`contracts/LRTWithdrawalManager.sol`)

### Summary
`_withdrawFromAave` caps withdrawable amount to `min(aaveBalance, totalETHDepositedToAave)`. When `totalETHDepositedToAave = 0` (all principal previously withdrawn) but `aaveAWETH.balanceOf > 0` (interest accrued), the function returns 0 ETH. `_processWithdrawalCompletion` then reverts with `InsufficientLiquidityForWithdrawal`, permanently blocking all pending ETH withdrawal completions.

### Finding Description

In `_withdrawFromAave`:

```solidity
uint256 withdrawablePrincipal = aaveBalance < totalETHDepositedToAave
    ? aaveBalance
    : totalETHDepositedToAave;

withdrawnAmount = amount > withdrawablePrincipal ? withdrawablePrincipal : amount;
if (withdrawnAmount == 0) return 0;  // silent return when totalETHDepositedToAave = 0
``` [1](#0-0) 

When `totalETHDepositedToAave = 0`, `withdrawablePrincipal = 0`, `withdrawnAmount = 0`, and the function silently returns 0 regardless of how much aWETH the contract holds.

In `_processWithdrawalCompletion`, after `_withdrawFromAave` returns 0:

```solidity
uint256 balanceAfter = address(this).balance;
if (balanceAfter < request.expectedAssetAmount) {
    revert InsufficientLiquidityForWithdrawal();
}
``` [2](#0-1) 

The revert is unconditional because no ETH was withdrawn from Aave.

The state `totalETHDepositedToAave = 0` with `aaveBalance > 0` is a normal operational outcome: deposit 100 ETH, interest accrues to 105, withdraw all 100 principal → `totalETHDepositedToAave = 0`, `aaveBalance = 5`. No admin compromise or malicious action is required.

No existing function can redirect the interest ETH to the contract for user withdrawals:
- `emergencyWithdrawFromAave` calls `_collectInterestToTreasury()` (interest → treasury) then `_withdrawFromAave` (returns 0). [3](#0-2) 
- `setAaveIntegrationEnabled(false)` also routes interest to treasury, not to the contract. [4](#0-3) 
- `_collectInterestToTreasury` explicitly sends interest to treasury, not to `address(this)`. [5](#0-4) 

### Impact Explanation

User rsETH is burned in `unlockQueue` before `completeWithdrawal` is called. Once burned, the user holds no rsETH and their only recourse is `completeWithdrawal`. If that permanently reverts, the user's ETH is frozen: rsETH is gone, ETH is inaccessible. This is a **Critical: Permanent freezing of funds**.

### Likelihood Explanation

The triggering state arises through normal protocol operation (deposit → interest accrues → withdraw all principal). No attacker action is needed; any user whose withdrawal is unlocked after this state is reached is affected. Likelihood is **Medium** (requires a specific but naturally occurring accounting state).

### Recommendation

Remove the principal-only cap in `_withdrawFromAave` when called from `_processWithdrawalCompletion`, or allow the function to use accrued interest when `totalETHDepositedToAave = 0`. One approach: if `withdrawablePrincipal == 0` and `aaveBalance > 0`, allow withdrawing up to `aaveBalance` (treating residual balance as reclaimable for user obligations). Alternatively, track interest separately and allow it to be used for withdrawal completion before routing to treasury.

### Proof of Concept

```solidity
// State setup (achievable through normal operation):
// totalETHDepositedToAave = 0
// aaveAWETH.balanceOf(withdrawalManager) = 5e18  (pure interest)
// address(withdrawalManager).balance = 0
// isAaveIntegrationEnabled = true
// User has an unlocked withdrawal request for 1e18 ETH (rsETH already burned)

// Call: withdrawalManager.completeWithdrawal(ETH_TOKEN, "")
// Execution:
//   contractBalance = 0 < 1e18 = request.expectedAssetAmount
//   amountNeeded = 1e18
//   _withdrawFromAave(1e18):
//     aaveBalance = 5e18, totalETHDepositedToAave = 0
//     withdrawablePrincipal = min(5e18, 0) = 0
//     withdrawnAmount = min(1e18, 0) = 0
//     returns 0
//   balanceAfter = 0 < 1e18
//   → revert InsufficientLiquidityForWithdrawal
// User cannot recover funds. All retry attempts revert identically.
```

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L486-501)
```text
        if (!enabled) {
            uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
            if (aaveBalance > 0) {
                // First collect any accrued interest to treasury
                _collectInterestToTreasury();

                // Then withdraw remaining principal from Aave back to contract
                aaveBalance = aaveAWETH.balanceOf(address(this));
                if (aaveBalance > 0) {
                    _withdrawFromAave(aaveBalance);
                }
            }

            // Revoke approval for aWETH token to Aave WETH Gateway
            _revokeApprovalToAaveWETHGateway();
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

**File:** contracts/LRTWithdrawalManager.sol (L727-730)
```text
                uint256 balanceAfter = address(this).balance;
                if (balanceAfter < request.expectedAssetAmount) {
                    revert InsufficientLiquidityForWithdrawal();
                }
```

**File:** contracts/LRTWithdrawalManager.sol (L911-915)
```text
        // Only withdraw up to the principal amount (don't use accrued interest for user withdrawals)
        uint256 withdrawablePrincipal = aaveBalance < totalETHDepositedToAave ? aaveBalance : totalETHDepositedToAave;

        withdrawnAmount = amount > withdrawablePrincipal ? withdrawablePrincipal : amount;
        if (withdrawnAmount == 0) return 0;
```

**File:** contracts/LRTWithdrawalManager.sol (L945-958)
```text
    function _collectInterestToTreasury() internal returns (uint256 interestAmount) {
        uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
        uint256 principal = totalETHDepositedToAave;

        // Return 0 if no interest or balance is less than principal (accounting for rounding)
        if (aaveBalance <= principal) return 0;

        interestAmount = aaveBalance - principal;

        aaveWETHGateway.withdrawETH(aavePool, interestAmount, address(this));

        address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        (bool sent,) = payable(treasury).call{ value: interestAmount }("");
        if (!sent) revert TreasuryTransferFailed();
```
