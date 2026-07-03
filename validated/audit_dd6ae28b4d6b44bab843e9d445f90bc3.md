I have all the code needed. Let me analyze the exact execution path.

### Title
Emergency Withdrawal Blocked by Mandatory Treasury Transfer in `_collectInterestToTreasury` — (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

`emergencyWithdrawFromAave` unconditionally calls `_collectInterestToTreasury()` before withdrawing principal. If any accrued interest exists, `_collectInterestToTreasury` withdraws that interest from Aave and then hard-requires a successful ETH push to the treasury. If the treasury contract cannot receive ETH, the entire emergency withdrawal reverts, leaving ETH locked in Aave with no alternative recovery path.

---

### Finding Description

`emergencyWithdrawFromAave` is the sole privileged escape hatch for recovering ETH from Aave during an emergency: [1](#0-0) 

It unconditionally calls `_collectInterestToTreasury()` before withdrawing principal. Inside that function, once interest is detected (`aaveBalance > totalETHDepositedToAave`), it:

1. Calls `aaveWETHGateway.withdrawETH` to pull the interest amount out of Aave into the contract.
2. Pushes that ETH to the treasury via a low-level call.
3. **Hard-reverts** if the push fails: [2](#0-1) 

If `payable(treasury).call{ value: interestAmount }("")` returns `false` (treasury is a contract that reverts on `receive`, is paused, or has no fallback), the `TreasuryTransferFailed` revert propagates all the way up through `emergencyWithdrawFromAave`. The principal ETH remains in Aave and cannot be recovered.

There is no alternative recovery path: both `setAaveIntegrationEnabled(false)` and `configureAaveIntegration` also call `_collectInterestToTreasury()` unconditionally before withdrawing: [3](#0-2) [4](#0-3) 

---

### Impact Explanation

When `emergencyWithdrawFromAave` is blocked, ETH committed to Aave cannot be returned to `LRTWithdrawalManager`. The contract's ETH balance stays at zero, so `unlockQueue` cannot fund pending withdrawal requests and users cannot complete ETH withdrawals. This constitutes **temporary freezing of user ETH withdrawal funds** (Medium scope).

---

### Likelihood Explanation

The preconditions are:

1. **Accrued interest exists** — guaranteed after any non-trivial time with Aave integration enabled; `aaveBalance > totalETHDepositedToAave` is the normal steady state.
2. **Treasury cannot receive ETH** — the `PROTOCOL_TREASURY` address is a protocol-controlled smart contract (multisig, treasury proxy, etc.). Such contracts can temporarily fail to accept ETH due to a bug, an in-progress upgrade, a paused state, or simply lacking a `receive()` function. This is not an attacker-controlled condition, but it is a realistic operational scenario.

No attacker action is required; the freeze occurs whenever both conditions coincide.

---

### Recommendation

Decouple the interest collection from the emergency withdrawal. Options:

- Skip `_collectInterestToTreasury()` entirely inside `emergencyWithdrawFromAave` (interest remains as part of the withdrawn balance and can be accounted for separately).
- Wrap the call in a `try/catch` or check-and-skip pattern: if the treasury transfer fails, leave the interest in the contract rather than reverting.
- Add a dedicated `emergencyWithdrawAllFromAave` that bypasses interest collection and withdraws the full `aaveAWETH` balance directly.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.21;

// Fork test (local Anvil fork of mainnet or Aave testnet)
// 1. Deploy a mock treasury that reverts on receive:
contract RevertingTreasury {
    receive() external payable { revert("no ETH"); }
}

// 2. Set PROTOCOL_TREASURY to RevertingTreasury in LRTConfig.
// 3. Deposit ETH into LRTWithdrawalManager and enable Aave integration.
// 4. Warp time forward so Aave accrues interest
//    (aaveAWETH.balanceOf(withdrawalManager) > totalETHDepositedToAave).
// 5. Call emergencyWithdrawFromAave(type(uint256).max) as PAUSER_ROLE.
// 6. Assert: call reverts with TreasuryTransferFailed.
// 7. Assert: aaveAWETH.balanceOf(withdrawalManager) is unchanged (ETH still in Aave).
// 8. Assert: no other function can recover the ETH
//    (setAaveIntegrationEnabled(false) and configureAaveIntegration also revert).
```

The revert at [5](#0-4)  propagates through `emergencyWithdrawFromAave` at [6](#0-5) , leaving the PAUSER_ROLE with no functional escape hatch and user ETH withdrawals frozen.

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

**File:** contracts/LRTWithdrawalManager.sol (L954-958)
```text
        aaveWETHGateway.withdrawETH(aavePool, interestAmount, address(this));

        address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        (bool sent,) = payable(treasury).call{ value: interestAmount }("");
        if (!sent) revert TreasuryTransferFailed();
```
