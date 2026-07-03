### Title
Reverting Treasury Address Permanently Freezes Aave-Accrued Yield and Blocks Emergency Withdrawal — (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

`_collectInterestToTreasury()` uses a push-ETH pattern: it first withdraws interest from Aave into the contract, then pushes ETH to the treasury via a low-level call. If the treasury address is a contract that reverts on ETH receipt, the entire transaction reverts (including the Aave withdrawal), making the interest permanently uncollectable. Critically, this also bricks `emergencyWithdrawFromAave()` and `setAaveIntegrationEnabled(false)`, both of which unconditionally call `_collectInterestToTreasury()` before acting on principal.

---

### Finding Description

The internal function `_collectInterestToTreasury()` executes the following sequence atomically:

1. Computes `interestAmount = aaveAWETH.balanceOf(address(this)) - totalETHDepositedToAave`
2. Calls `aaveWETHGateway.withdrawETH(aavePool, interestAmount, address(this))` — ETH lands in the contract
3. Calls `payable(treasury).call{ value: interestAmount }("")`
4. If `sent == false`, reverts with `TreasuryTransferFailed` [1](#0-0) 

Because step 4 reverts the entire transaction, step 2 is also rolled back — the ETH does **not** get stuck in `LRTWithdrawalManager`; it remains as aWETH in Aave. However, every future call to collect that interest will follow the same path and revert identically, permanently freezing the yield.

The treasury address is read dynamically from `lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY)`, which is set by `DEFAULT_ADMIN_ROLE` via `LRTConfig.setContract()`. [2](#0-1) 

Three callers of `_collectInterestToTreasury()` are affected:

| Caller | Effect if treasury reverts |
|---|---|
| `collectInterestToTreasury()` (line 544) | Always reverts — yield never leaves Aave |
| `emergencyWithdrawFromAave()` (line 558) | Always reverts — **principal also unrecoverable** |
| `setAaveIntegrationEnabled(false)` (line 490) | Always reverts — integration cannot be disabled | [3](#0-2) [4](#0-3) 

---

### Impact Explanation

- **Primary (Medium):** Permanent freezing of unclaimed yield. All Aave-accrued interest is unclaimable for as long as the treasury address reverts on ETH receipt.
- **Secondary (escalates to Critical):** `emergencyWithdrawFromAave()` is gated behind `_collectInterestToTreasury()` with no bypass. If the treasury is broken and interest has accrued, the PAUSER_ROLE cannot recover principal ETH from Aave in an emergency, potentially causing permanent freezing of principal funds.

---

### Likelihood Explanation

The treasury address is admin-controlled and is not required to be an EOA. A realistic trigger is a treasury contract upgrade that removes or breaks the `receive()` function, or a multisig wallet that is bricked. This is not an attacker-controlled path, but it is a realistic operational scenario that the protocol code should handle gracefully. The protocol has no validation that the treasury can accept ETH before withdrawing from Aave.

---

### Recommendation

Replace the push pattern with a pull pattern for treasury ETH:

1. After `withdrawETH`, store the interest amount in a dedicated `pendingTreasuryETH` state variable instead of immediately pushing.
2. Expose a separate `claimTreasuryETH()` function that the treasury can call to pull its balance.
3. Alternatively, if the push pattern is retained, wrap the treasury call in a `try/catch`-equivalent (check-and-skip on failure) and emit an event, so that `emergencyWithdrawFromAave()` and `setAaveIntegrationEnabled(false)` are never blocked by a treasury transfer failure.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// Mock treasury that reverts on ETH receive
contract RevertingTreasury {
    receive() external payable { revert("no ETH"); }
}

// Fork test outline (Foundry)
function test_frozenYield_revertingTreasury() public {
    // 1. Deploy RevertingTreasury and set as PROTOCOL_TREASURY in lrtConfig
    RevertingTreasury badTreasury = new RevertingTreasury();
    vm.prank(admin);
    lrtConfig.setContract(LRTConstants.PROTOCOL_TREASURY, address(badTreasury));

    // 2. Ensure Aave integration is enabled and interest has accrued
    //    (aaveAWETH.balanceOf(withdrawalManager) > totalETHDepositedToAave)
    vm.roll(block.number + 1000); // simulate time passing for interest accrual

    // 3. collectInterestToTreasury always reverts
    vm.prank(operator);
    vm.expectRevert(ILRTWithdrawalManager.TreasuryTransferFailed.selector);
    withdrawalManager.collectInterestToTreasury();

    // 4. emergencyWithdrawFromAave also always reverts
    vm.prank(pauser);
    vm.expectRevert(ILRTWithdrawalManager.TreasuryTransferFailed.selector);
    withdrawalManager.emergencyWithdrawFromAave(type(uint256).max);

    // 5. setAaveIntegrationEnabled(false) also always reverts
    vm.prank(manager);
    vm.expectRevert(ILRTWithdrawalManager.TreasuryTransferFailed.selector);
    withdrawalManager.setAaveIntegrationEnabled(false);

    // Interest remains frozen in Aave indefinitely
    assertGt(
        aaveAWETH.balanceOf(address(withdrawalManager)) - withdrawalManager.totalETHDepositedToAave(),
        0
    );
}
```

The root cause is the unconditional `revert TreasuryTransferFailed` at line 958 with no fallback path, combined with `emergencyWithdrawFromAave` having no way to skip the interest collection step. [5](#0-4)

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

**File:** contracts/LRTConfig.sol (L237-251)
```text
    function setContract(bytes32 contractKey, address contractAddress) external onlyRole(DEFAULT_ADMIN_ROLE) {
        _setContract(contractKey, contractAddress);
    }

    /// @dev private function to set a contract
    /// @param key Contract key
    /// @param val Contract address
    function _setContract(bytes32 key, address val) private {
        UtilLib.checkNonZeroAddress(val);
        if (contractMap[key] == val) {
            revert ValueAlreadyInUse();
        }
        contractMap[key] = val;
        emit SetContract(key, val);
    }
```
