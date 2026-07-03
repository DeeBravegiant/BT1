The code is clear. Let me trace the full execution path precisely.

### Title
Emergency Aave Withdrawal Blocked by Non-Payable Treasury — Temporary Freeze of All Aave Funds - (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

`emergencyWithdrawFromAave` unconditionally calls `_collectInterestToTreasury()` before withdrawing principal. If `PROTOCOL_TREASURY` is set to a non-payable contract and Aave has accrued interest, `_collectInterestToTreasury()` reverts with `TreasuryTransferFailed`, blocking the emergency withdrawal entirely. Every other Aave exit path has the same dependency, so all Aave funds (principal + yield) are frozen until the treasury address is corrected.

---

### Finding Description

`emergencyWithdrawFromAave` calls `_collectInterestToTreasury()` unconditionally at line 558 before withdrawing principal: [1](#0-0) 

`_collectInterestToTreasury()` only skips the treasury transfer when `aaveBalance <= principal` (no interest). When interest exists, it withdraws the interest from Aave and then pushes ETH to the treasury via a low-level call. If that call fails, the entire transaction reverts: [2](#0-1) 

`PROTOCOL_TREASURY` is a plain `contractMap` entry updatable by `DEFAULT_ADMIN_ROLE` with no payability check: [3](#0-2) 

Every Aave exit path shares the same blocking dependency:

| Function | Line calling `_collectInterestToTreasury()` |
|---|---|
| `emergencyWithdrawFromAave` | 558 |
| `setAaveIntegrationEnabled(false)` | 490 |
| `configureAaveIntegration` (reconfigure) | 442 | [4](#0-3) [5](#0-4) 

There is no direct call path to `_withdrawFromAave` that bypasses `_collectInterestToTreasury()`.

---

### Impact Explanation

**Impact: Medium — Temporary freezing of funds.**

The question claims "permanent" freezing, but this is incorrect. `DEFAULT_ADMIN_ROLE` can call `LRTConfig.setContract(LRTConstants.PROTOCOL_TREASURY, payableAddress)` to replace the non-payable treasury with a payable one, after which all three exit paths become functional again. The freeze is therefore temporary — bounded by the time required for the admin (potentially behind a multisig/timelock) to execute the treasury update. During that window, both the principal and accrued yield are inaccessible, including through the emergency path that is supposed to be unconditionally available to `PAUSER_ROLE`.

---

### Likelihood Explanation

**Likelihood: Low-Medium.**

- Requires `DEFAULT_ADMIN_ROLE` to set `PROTOCOL_TREASURY` to a non-payable address. This is an accidental misconfiguration (e.g., a governance multisig or a contract without a `receive()` function), not malicious compromise.
- Aave interest accrues passively; no attacker action is needed to satisfy the second condition.
- The combination is realistic in production: treasury addresses are commonly updated to governance contracts that do not implement `receive()`.

---

### Recommendation

1. In `_collectInterestToTreasury()`, handle a failed treasury transfer gracefully instead of reverting — e.g., leave the interest in the contract and emit an event, rather than reverting the entire call.
2. Alternatively, in `emergencyWithdrawFromAave`, skip `_collectInterestToTreasury()` entirely (or wrap it in a `try/catch`) so that principal recovery is never gated on treasury reachability.
3. Add a payability check when setting `PROTOCOL_TREASURY` in `LRTConfig.setContract`.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// NonPayableTreasury: no receive() or fallback()
contract NonPayableTreasury {}

// In a fork/local test:
// 1. Deploy NonPayableTreasury
// 2. lrtConfig.setContract(LRTConstants.PROTOCOL_TREASURY, address(nonPayableTreasury));
// 3. Let Aave accrue interest (warp time or mock aaveAWETH.balanceOf > totalETHDepositedToAave)
// 4. vm.prank(pauserRole);
//    vm.expectRevert(LRTWithdrawalManager.TreasuryTransferFailed.selector);
//    withdrawalManager.emergencyWithdrawFromAave(type(uint256).max);
// 5. Also assert setAaveIntegrationEnabled(false) and configureAaveIntegration() revert identically.
// 6. Confirm principal remains locked in Aave until treasury is updated to a payable address.
```

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

**File:** contracts/LRTWithdrawalManager.sol (L950-958)
```text
        if (aaveBalance <= principal) return 0;

        interestAmount = aaveBalance - principal;

        aaveWETHGateway.withdrawETH(aavePool, interestAmount, address(this));

        address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        (bool sent,) = payable(treasury).call{ value: interestAmount }("");
        if (!sent) revert TreasuryTransferFailed();
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
