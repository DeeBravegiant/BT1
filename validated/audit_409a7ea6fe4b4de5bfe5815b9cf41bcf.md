### Title
Treasury ETH-Rejection Blocks Aave Integration Disable/Reconfigure, Temporarily Freezing ETH — (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

`_collectInterestToTreasury()` is called unconditionally inside both `setAaveIntegrationEnabled(false)` and `configureAaveIntegration(...)`. If the `PROTOCOL_TREASURY` address is a contract that rejects ETH (no `receive`/`fallback`), the hard `revert TreasuryTransferFailed` at line 958 causes both admin escape-hatch functions to revert, leaving all ETH deposited to Aave permanently inaccessible until the treasury address is changed.

---

### Finding Description

`_collectInterestToTreasury()` performs two sequential external calls:

1. Withdraws accrued interest from Aave into the contract: [1](#0-0) 

2. Pushes that ETH to the treasury address with a bare `.call`: [2](#0-1) 

If `sent == false` the function reverts, rolling back the entire transaction — including the Aave withdrawal — so the ETH stays locked in Aave.

This internal function is called unconditionally in **three** operator/manager paths:

- `setAaveIntegrationEnabled(false)` — line 490: [3](#0-2) 

- `configureAaveIntegration(...)` — line 442: [4](#0-3) 

- `emergencyWithdrawFromAave(...)` — line 558: [5](#0-4) 

The treasury address is read from `LRTConfig.contractMap[PROTOCOL_TREASURY]`, set via `LRTConfig.setContract` which requires `DEFAULT_ADMIN_ROLE`: [6](#0-5) 

A realistic treasury is a DAO governance contract, a Gnosis Safe, or any protocol contract that handles only ERC-20 tokens and has no `receive()` function. This is not a malicious configuration — it is a common deployment pattern.

---

### Impact Explanation

When the precondition holds (`isAaveIntegrationEnabled == true` and `aaveAWETH.balanceOf(address(this)) > totalETHDepositedToAave`):

- Every call to `setAaveIntegrationEnabled(false)` reverts → `isAaveIntegrationEnabled` stays `true`.
- Every call to `configureAaveIntegration(...)` reverts → addresses cannot be updated.
- Every call to `emergencyWithdrawFromAave(...)` reverts → the emergency path is also blocked.

All ETH deposited to Aave is inaccessible for user withdrawals until the admin updates the treasury address in `LRTConfig` to an ETH-accepting address. This is **temporary freezing of funds** (Medium impact per scope rules).

---

### Likelihood Explanation

- Treasury contracts that do not accept raw ETH are common (multisigs configured for ERC-20 only, DAO vaults, etc.).
- Aave interest accrues automatically over time; no attacker action is needed.
- The condition is reachable in normal production operation without any malicious actor.

---

### Recommendation

Decouple the treasury transfer from the disable/reconfigure flow. Two options:

1. **Accumulate interest in-contract**: When called from `setAaveIntegrationEnabled` or `configureAaveIntegration`, withdraw the interest ETH from Aave but hold it in the contract (tracked separately) rather than pushing it to treasury. A separate `collectInterestToTreasury()` call can then be retried independently.

2. **Soft-fail the treasury push**: If the treasury transfer fails, emit an event and leave the ETH in the contract rather than reverting. This prevents a bad treasury address from blocking the disable path.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// Treasury contract that rejects ETH
contract RejectingTreasury {
    // No receive() or fallback() — all ETH transfers revert
}

// Test (pseudocode for a Foundry fork test):
// 1. Deploy RejectingTreasury
// 2. lrtConfig.setContract(LRTConstants.PROTOCOL_TREASURY, address(rejectingTreasury))
//    (requires DEFAULT_ADMIN_ROLE)
// 3. Advance time so Aave interest accrues:
//    assert aaveAWETH.balanceOf(address(withdrawalManager)) > withdrawalManager.totalETHDepositedToAave()
// 4. Call withdrawalManager.setAaveIntegrationEnabled(false) as LRT manager
//    → reverts with TreasuryTransferFailed
// 5. Call withdrawalManager.emergencyWithdrawFromAave(type(uint256).max) as PAUSER_ROLE
//    → also reverts with TreasuryTransferFailed
// 6. ETH remains locked in Aave; user withdrawal claims for ETH cannot be serviced
```

The root cause is at: [7](#0-6)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L438-449)
```text
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

**File:** contracts/LRTWithdrawalManager.sol (L486-497)
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
