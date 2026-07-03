### Title
Aave Dependency in `completeWithdrawal()` Chains External Calls Without Fallback, Temporarily Freezing ETH Withdrawals - (File: contracts/LRTWithdrawalManager.sol)

---

### Summary

When Aave integration is enabled, `completeWithdrawal()` for ETH chains an external call to `aaveWETHGateway.withdrawETH()` with the user's ETH transfer in a single transaction. If Aave is paused or unavailable, the external call reverts and all ETH withdrawals are blocked. The intended recovery paths (`setAaveIntegrationEnabled(false)` and `emergencyWithdrawFromAave()`) also depend on Aave being available, creating a complete deadlock with no on-chain escape hatch.

---

### Finding Description

`_processWithdrawalCompletion()`, called by the public `completeWithdrawal()`, contains the following logic when Aave integration is enabled and the contract's native ETH balance is insufficient:

```solidity
if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN) {
    uint256 contractBalance = address(this).balance;
    if (contractBalance < request.expectedAssetAmount) {
        uint256 amountNeeded = request.expectedAssetAmount - contractBalance;
        _withdrawFromAave(amountNeeded);          // ← external call 1
        ...
    }
}
_transferAsset(asset, user, request.expectedAssetAmount); // ← external call 2
```

`_withdrawFromAave()` calls `aaveWETHGateway.withdrawETH(aavePool, withdrawnAmount, address(this))`. If Aave is paused, this reverts, and the entire `completeWithdrawal()` transaction reverts. Since most deposited ETH is expected to be in Aave (that is the purpose of the integration), the `contractBalance < request.expectedAssetAmount` condition will be true for virtually all users, making the Aave call mandatory.

The two intended recovery paths are equally blocked:

**`setAaveIntegrationEnabled(false)`** calls `_collectInterestToTreasury()` then `_withdrawFromAave()`, both of which call `aaveWETHGateway.withdrawETH()`: [1](#0-0) 

**`emergencyWithdrawFromAave()`** calls `_collectInterestToTreasury()` first, then `_withdrawFromAave()`: [2](#0-1) 

`_collectInterestToTreasury()` calls `aaveWETHGateway.withdrawETH()` when interest has accrued, and `_withdrawFromAave()` always calls it: [3](#0-2) [4](#0-3) 

There is no code path that allows the protocol to disable Aave integration or unblock user withdrawals while Aave itself is unavailable.

---

### Impact Explanation

Users who have initiated ETH withdrawals (burning their rsETH in `initiateWithdrawal`) and whose requests have been unlocked by the operator cannot complete their withdrawals. Their rsETH is already burned; they hold no claim token. The ETH owed to them is locked in Aave and inaccessible through any on-chain path until Aave becomes available again. This constitutes **temporary freezing of funds** (Medium severity).

---

### Likelihood Explanation

Aave v3 has a guardian role that can pause the protocol in response to security incidents. Aave has been paused on mainnet in the past. When Aave is paused, `withdrawETH` on the WETH gateway reverts. The Aave integration is explicitly designed to hold the majority of the ETH balance, so the Aave call path in `completeWithdrawal()` is the normal execution path, not an edge case. Likelihood is **Medium**.

---

### Recommendation

1. **Isolate the Aave withdrawal from the user payout**: Separate the Aave withdrawal into a distinct preparatory step (pull-over-push). Operators should be able to call a function that withdraws ETH from Aave into the contract, and users then claim from the contract's native balance.

2. **Add a fallback in `setAaveIntegrationEnabled(false)`**: Use a `try/catch` around the Aave withdrawal calls so that the integration can be disabled even when Aave is unavailable, leaving the ETH to be recovered later.

3. **Decouple `emergencyWithdrawFromAave()` from `_collectInterestToTreasury()`**: The emergency function should not be blocked by interest collection. Interest collection should be skippable in emergency scenarios.

---

### Proof of Concept

1. Protocol enables Aave integration; operators deposit accumulated ETH into Aave via `depositIdleETHToAave()`. The contract's native ETH balance drops to near zero.
2. A user's ETH withdrawal request is unlocked by the operator.
3. Aave governance pauses the Aave v3 pool (a real, documented capability).
4. User calls `completeWithdrawal(ETH_TOKEN, "")`.
5. `_processWithdrawalCompletion()` detects `address(this).balance < request.expectedAssetAmount`, calls `_withdrawFromAave()`, which calls `aaveWETHGateway.withdrawETH()` → **reverts** because Aave is paused.
6. Manager calls `setAaveIntegrationEnabled(false)` to unblock users → calls `_collectInterestToTreasury()` → calls `aaveWETHGateway.withdrawETH()` → **reverts**.
7. Pauser calls `emergencyWithdrawFromAave()` → calls `_collectInterestToTreasury()` → **reverts** (if interest > 0) or calls `_withdrawFromAave()` → **reverts**.
8. All ETH withdrawals remain frozen for the duration of the Aave pause with no on-chain remedy. [5](#0-4) [6](#0-5)

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

**File:** contracts/LRTWithdrawalManager.sol (L719-734)
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
