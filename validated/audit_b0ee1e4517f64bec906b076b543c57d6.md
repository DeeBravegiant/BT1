### Title
Emergency Aave Withdrawal Blocked by Reverting Treasury — (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

`emergencyWithdrawFromAave` unconditionally calls `_collectInterestToTreasury()` before executing the actual withdrawal. If the `PROTOCOL_TREASURY` address is a contract that reverts on ETH receipt (e.g., a multisig whose signers are unavailable), the entire emergency path reverts and ETH deposited to Aave cannot be recovered.

---

### Finding Description

`emergencyWithdrawFromAave` is gated to `PAUSER_ROLE` and is intended to always be executable in crisis scenarios. However, it calls `_collectInterestToTreasury()` as a mandatory first step: [1](#0-0) 

Inside `_collectInterestToTreasury`, when `aaveBalance > totalETHDepositedToAave` (i.e., interest has accrued), the function:
1. Withdraws the interest from Aave via `aaveWETHGateway.withdrawETH` (line 954)
2. Attempts to push ETH to the treasury via a low-level call (line 957)
3. **Hard-reverts** if the call fails (line 958) [2](#0-1) 

Because Solidity reverts roll back all state changes, the Aave withdrawal at line 954 is also undone — the ETH remains locked in Aave and the emergency path is completely unavailable.

The treasury address is fetched dynamically from `lrtConfig`: [3](#0-2) 

If `PROTOCOL_TREASURY` is a multisig that has become non-functional (e.g., quorum unreachable, contract bug, upgrade gone wrong), every call to `emergencyWithdrawFromAave` will revert as long as any interest has accrued — which is guaranteed over time.

---

### Impact Explanation

ETH deposited to Aave via `LRTWithdrawalManager` cannot be recovered through the designated emergency path. Withdrawal requests that depend on that ETH liquidity cannot be fulfilled. This constitutes **temporary freezing of funds** (Medium), since the block persists until either the treasury is fixed or the treasury address is updated — both of which may themselves be gated behind the same broken multisig.

---

### Likelihood Explanation

- Interest accrues automatically in Aave over time, so `aaveBalance > totalETHDepositedToAave` is the normal steady state after any meaningful deposit period.
- Multisig treasuries becoming temporarily non-functional (key loss, signer unavailability, contract upgrade issues) is a well-documented operational risk.
- No attacker action is required; the condition arises from normal protocol operation combined with an operational failure of the treasury contract.

---

### Recommendation

Decouple interest collection from the emergency withdrawal path. The simplest fix is to wrap the interest-collection step in a `try/catch` (or skip it entirely) inside `emergencyWithdrawFromAave`, so that a non-functional treasury cannot block principal recovery:

```solidity
function emergencyWithdrawFromAave(uint256 amount) external nonReentrant onlyRole(LRTConstants.PAUSER_ROLE) {
    if (!isAaveIntegrationEnabled) revert AaveIntegrationNotEnabled();

    uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
    if (aaveBalance == 0) revert InsufficientAaveBalance();

    // Best-effort interest collection — must not block emergency path
    try this._collectInterestToTreasuryExternal() {} catch {}

    uint256 withdrawnAmount = _withdrawFromAave(amount);
    emit EmergencyWithdrawFromAave(withdrawnAmount, address(this));
}
```

Alternatively, simply omit the interest-collection step from `emergencyWithdrawFromAave` entirely and let operators call `collectInterestToTreasury` separately when the treasury is healthy.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.21;

// RevertingTreasury: simulates a broken multisig
contract RevertingTreasury {
    receive() external payable { revert("broken"); }
}

// In a Foundry fork test:
// 1. Deploy RevertingTreasury
// 2. lrtConfig.setContract(LRTConstants.PROTOCOL_TREASURY, address(revertingTreasury))
// 3. Seed LRTWithdrawalManager with ETH, deposit to Aave, advance time so interest accrues
//    (aaveAWETH.balanceOf(withdrawalManager) > totalETHDepositedToAave)
// 4. vm.prank(pauserRole); withdrawalManager.emergencyWithdrawFromAave(type(uint256).max);
// 5. Assert: call reverts with TreasuryTransferFailed
//    → emergency path is completely blocked despite PAUSER_ROLE authorization
```

The revert at `_collectInterestToTreasury` line 958 propagates through `emergencyWithdrawFromAave` line 558, making the emergency withdrawal unavailable for as long as interest exists and the treasury cannot receive ETH.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L557-560)
```text
        // First collect any accrued interest to treasury
        _collectInterestToTreasury();

        uint256 withdrawnAmount = _withdrawFromAave(amount);
```

**File:** contracts/LRTWithdrawalManager.sol (L954-958)
```text
        aaveWETHGateway.withdrawETH(aavePool, interestAmount, address(this));

        address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        (bool sent,) = payable(treasury).call{ value: interestAmount }("");
        if (!sent) revert TreasuryTransferFailed();
```
