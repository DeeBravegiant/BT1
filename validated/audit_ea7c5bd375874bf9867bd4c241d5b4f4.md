### Title
Silent Zero-Return in `_withdrawFromAave` When `totalETHDepositedToAave == 0` With Non-Zero aWETH Balance Causes Temporary Freezing of User ETH Withdrawals — (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

`_withdrawFromAave` computes `withdrawablePrincipal = min(aaveBalance, totalETHDepositedToAave)`. When `totalETHDepositedToAave == 0` but `aaveAWETH.balanceOf(address(this)) > 0` (reachable via a permissionless aWETH donation), the function passes the `aaveBalance == 0` guard, computes `withdrawablePrincipal = 0`, and silently returns `0`. The caller `_processWithdrawalCompletion` then reverts with `InsufficientLiquidityForWithdrawal`, blocking all pending ETH user withdrawals.

---

### Finding Description

**Root cause — `_withdrawFromAave`:** [1](#0-0) 

```solidity
function _withdrawFromAave(uint256 amount) internal returns (uint256 withdrawnAmount) {
    if (amount == 0) return 0;

    uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
    if (aaveBalance == 0) revert InsufficientAaveBalance();   // ← passes when aaveBalance > 0

    uint256 withdrawablePrincipal = aaveBalance < totalETHDepositedToAave
        ? aaveBalance
        : totalETHDepositedToAave;                            // ← = min(aaveBalance, 0) = 0

    withdrawnAmount = amount > withdrawablePrincipal ? withdrawablePrincipal : amount;
    if (withdrawnAmount == 0) return 0;                       // ← silent return, no revert
    ...
}
```

When `totalETHDepositedToAave == 0` and `aaveBalance > 0`:
- The `aaveBalance == 0` guard is **not** triggered (aaveBalance is positive).
- `withdrawablePrincipal = min(aaveBalance, 0) = 0`.
- `withdrawnAmount = 0`, and the function **returns 0 silently** without withdrawing anything.

**Caller — `_processWithdrawalCompletion`:** [2](#0-1) 

```solidity
if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN) {
    uint256 contractBalance = address(this).balance;
    if (contractBalance < request.expectedAssetAmount) {
        uint256 amountNeeded = request.expectedAssetAmount - contractBalance;
        _withdrawFromAave(amountNeeded);          // returns 0, nothing withdrawn

        uint256 balanceAfter = address(this).balance;
        if (balanceAfter < request.expectedAssetAmount) {
            revert InsufficientLiquidityForWithdrawal();  // ← always reverts
        }
    }
}
```

The return value of `_withdrawFromAave` is not checked; the subsequent balance check catches the failure and reverts, blocking the user's withdrawal.

**How `totalETHDepositedToAave == 0` with `aaveBalance > 0` is reached:**

*Donation path (permissionless, no admin required):*
aWETH is a standard ERC-20. Any party can acquire aWETH (by depositing WETH into Aave) and `transfer` it directly to `LRTWithdrawalManager`. This bypasses `_depositToAave`, so `totalETHDepositedToAave` is never incremented. [3](#0-2) 

`_depositToAave` is the only place `totalETHDepositedToAave` is incremented; a direct ERC-20 transfer to the contract does not call it.

*Natural rounding path (no attacker needed):*
After a full withdrawal cycle, Aave's internal rounding may leave 1–2 wei of aWETH while `totalETHDepositedToAave` reaches exactly 0. The `_checkAaveHealth` function explicitly tolerates up to 2 wei of discrepancy: [4](#0-3) 

but `_withdrawFromAave` does not handle this edge case.

**Recovery analysis:**

`_collectInterestToTreasury` treats the entire donated aWETH as "interest" (since `aaveBalance > principal = 0`) and withdraws it — but sends the resulting ETH to the **treasury**, not to the contract: [5](#0-4) 

This means the contract's ETH balance is not restored by this call. The treasury admin must then manually return ETH to the contract before users can complete withdrawals. `LRTWithdrawalManager` does not inherit `Recoverable`, so there is no `recoverTokens` escape hatch for the donated aWETH. [6](#0-5) 

---

### Impact Explanation

All pending ETH withdrawal requests that require Aave liquidity (`contractBalance < request.expectedAssetAmount`) revert with `InsufficientLiquidityForWithdrawal`. Users cannot complete their withdrawals until an operator performs multi-step recovery (drain donated aWETH to treasury via `collectInterestToTreasury`, then treasury admin manually returns ETH to the contract). This constitutes **temporary freezing of user ETH withdrawal funds**.

---

### Likelihood Explanation

- The donation path is **permissionless**: any address can acquire aWETH on the open market and transfer it to the contract. The attacker loses the donated aWETH (it eventually goes to treasury), making this a griefing attack with a direct cost.
- The rounding path requires no attacker and can occur naturally after a full deposit/withdrawal cycle.
- The vulnerable state (`totalETHDepositedToAave == 0`, Aave integration enabled, contract ETH balance insufficient) is a realistic operational window, particularly just after Aave integration is first enabled or after a full principal withdrawal.

---

### Recommendation

Replace the silent `return 0` with a revert when `withdrawablePrincipal == 0` but `aaveBalance > 0`, or — preferably — allow withdrawal of the full `aaveBalance` regardless of `totalETHDepositedToAave` when the accounting variable has drifted to zero:

```solidity
// Use actual aWETH balance as the cap when totalETHDepositedToAave is zero
uint256 withdrawablePrincipal = totalETHDepositedToAave == 0
    ? aaveBalance
    : (aaveBalance < totalETHDepositedToAave ? aaveBalance : totalETHDepositedToAave);
```

Additionally, add a guard in `_processWithdrawalCompletion` to revert immediately if `_withdrawFromAave` returns less than `amountNeeded` rather than relying solely on the post-withdrawal balance check.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Foundry unit test (no mainnet fork required)
// Setup: deploy LRTWithdrawalManager with mocks; set totalETHDepositedToAave = 0;
//        mock aaveAWETH.balanceOf(contract) = 1 ether; isAaveIntegrationEnabled = true.

function test_donatedAWETH_freezesWithdrawal() public {
    // State: totalETHDepositedToAave = 0, aaveBalance = 1 ether (donated)
    vm.store(address(wm), totalETHDepositedToAaveSlot, bytes32(0));
    mockAWETH.setBalance(address(wm), 1 ether);

    // Queue and unlock a 0.5 ETH withdrawal request for a user
    // (contract ETH balance = 0, so Aave path is triggered)

    // Step 1: _withdrawFromAave returns 0 silently
    uint256 withdrawn = wm.exposed_withdrawFromAave(0.5 ether);
    assertEq(withdrawn, 0);

    // Step 2: completeWithdrawal reverts
    vm.expectRevert(ILRTWithdrawalManager.InsufficientLiquidityForWithdrawal.selector);
    wm.completeWithdrawal(ETH_TOKEN, "ref");
}
```

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L26-30)
```text
contract LRTWithdrawalManager is
    ILRTWithdrawalManager,
    LRTConfigRoleChecker,
    PausableUpgradeable,
    ReentrancyGuardUpgradeable
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

**File:** contracts/LRTWithdrawalManager.sol (L894-901)
```text
    function _depositToAave(uint256 amount) internal {
        if (amount == 0) return;

        aaveWETHGateway.depositETH{ value: amount }(aavePool, address(this), 0);
        totalETHDepositedToAave += amount;

        emit ETHDepositedToAave(amount, totalETHDepositedToAave);
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

**File:** contracts/LRTWithdrawalManager.sol (L945-961)
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

        emit InterestCollectedToTreasury(interestAmount, treasury);
    }
```
