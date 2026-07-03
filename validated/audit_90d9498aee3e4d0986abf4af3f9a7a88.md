### Title
Treasury ETH Transfer Revert in `_collectInterestToTreasury` Permanently Blocks Aave Integration Disable and Reconfiguration — (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

`_collectInterestToTreasury()` is called as a mandatory, non-skippable step inside `setAaveIntegrationEnabled(false)`, `configureAaveIntegration()`, and `emergencyWithdrawFromAave()`. If the `PROTOCOL_TREASURY` address is a contract that reverts on ETH receipt, all three functions permanently revert, freezing all accrued Aave yield (and blocking principal recovery) indefinitely.

---

### Finding Description

`_collectInterestToTreasury()` performs two sequential operations:

1. Withdraws the interest portion from Aave back to `LRTWithdrawalManager` via `aaveWETHGateway.withdrawETH(...)`.
2. Pushes the withdrawn ETH to `PROTOCOL_TREASURY` via a low-level `.call{value: interestAmount}("")`.

If step 2 fails (treasury reverts), the function reverts with `TreasuryTransferFailed`. Because the entire transaction is atomic, step 1 is also rolled back — the aWETH stays in Aave. [1](#0-0) 

This internal function is called unconditionally (no `try/catch`, no skip-if-zero guard beyond `aaveBalance <= principal`) in three places:

- `setAaveIntegrationEnabled(false)` — line 490
- `configureAaveIntegration()` — line 442
- `emergencyWithdrawFromAave()` — line 558 [2](#0-1) [3](#0-2) [4](#0-3) 

Once any interest has accrued (`aaveBalance > totalETHDepositedToAave`), all three functions will always revert as long as the treasury cannot accept ETH. There is no alternative code path to disable the integration, withdraw principal, or collect interest.

---

### Impact Explanation

- All accrued Aave yield is permanently frozen as aWETH — it can never be routed to the treasury.
- `setAaveIntegrationEnabled(false)` is permanently bricked, so the integration cannot be turned off.
- `configureAaveIntegration()` is permanently bricked, so the integration cannot be pointed at a new pool.
- `emergencyWithdrawFromAave()` is permanently bricked, eliminating the emergency recovery path.
- The principal deposited in Aave is also effectively frozen because `_withdrawFromAave` (which reduces `totalETHDepositedToAave`) is only reachable after `_collectInterestToTreasury()` succeeds.

Impact: **High — Theft of unclaimed yield** (yield permanently inaccessible to the treasury) / **Medium — Permanent freezing of unclaimed yield**.

---

### Likelihood Explanation

The `PROTOCOL_TREASURY` is a protocol-level config entry read from `lrtConfig`. Smart-contract treasuries (DAO vaults, multisigs with custom fallbacks, ERC-4626 vaults, contracts without `receive()`) are common in DeFi. A treasury contract that accepts ERC-20 tokens but not raw ETH is a realistic and non-adversarial deployment choice. No attacker action is required — the condition arises from a normal operational configuration.

---

### Recommendation

Decouple interest collection from the disable/reconfigure flow. Two complementary fixes:

1. **Wrap the treasury push in a try/catch or use a pull pattern.** If the treasury transfer fails, leave the withdrawn ETH in the contract (or in a dedicated claimable balance) rather than reverting the entire operation.

2. **Allow `setAaveIntegrationEnabled(false)` and `configureAaveIntegration()` to proceed even when interest collection fails.** Emit an event noting the uncollected interest so it can be claimed separately.

Example sketch for `_collectInterestToTreasury`:

```solidity
// Instead of reverting on failed push, accumulate for later pull
(bool sent,) = payable(treasury).call{ value: interestAmount }("");
if (!sent) {
    pendingTreasuryBalance += interestAmount;
    emit TreasuryTransferDeferred(interestAmount, treasury);
}
```

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// Treasury that reverts on ETH receive
contract RevertingTreasury {
    receive() external payable { revert("no ETH"); }
}

// Test sequence (Foundry pseudocode):
// 1. Deploy LRTWithdrawalManager with RevertingTreasury as PROTOCOL_TREASURY
// 2. configureAaveIntegration(pool, gateway, aWETH, dataProvider)
// 3. setAaveIntegrationEnabled(true)
// 4. Deposit ETH → Aave accrues interest (warp time / mock aWETH balance > totalETHDepositedToAave)
// 5. vm.expectRevert(LRTWithdrawalManager.TreasuryTransferFailed.selector);
//    withdrawalManager.setAaveIntegrationEnabled(false);  // always reverts
// 6. vm.expectRevert(LRTWithdrawalManager.TreasuryTransferFailed.selector);
//    withdrawalManager.emergencyWithdrawFromAave(type(uint256).max);  // always reverts
// 7. Assert: isAaveIntegrationEnabled == true (stuck), aWETH balance unchanged
``` [5](#0-4)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L437-449)
```text
        // If reconfiguring an existing Aave integration, collect interest and withdraw all funds first
        if (address(aaveAWETH) != address(0) && address(aaveWETHGateway) != address(0) && aavePool != address(0)) {
            uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
            if (aaveBalance > 0) {
                // First collect any accrued interest to treasury
                _collectInterestToTreasury();

                // Then withdraw all remaining principal from old Aave pool
                aaveBalance = aaveAWETH.balanceOf(address(this));
                if (aaveBalance > 0) {
                    _withdrawFromAave(aaveBalance);
                }
            }
```

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
