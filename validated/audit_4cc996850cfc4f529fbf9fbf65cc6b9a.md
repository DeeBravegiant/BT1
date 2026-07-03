### Title
`totalETHDepositedToAave` Not Decremented in `_withdrawFromAave`, Permanently Freezing Accrued Aave Interest — (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

`LRTWithdrawalManager` deposits idle ETH into Aave v3 to earn yield, tracking the principal via `totalETHDepositedToAave`. The internal `_withdrawFromAave` function is called in multiple code paths (user withdrawal completion, emergency withdrawal, disabling Aave integration) but does not decrement `totalETHDepositedToAave`. As a result, `getAccruedInterest()` permanently returns 0 after any withdrawal, and `collectInterestToTreasury()` can never collect the correct interest amount. The developer comment at line 642 explicitly acknowledges this: *"Returns 0 if aaveBalance < totalETHDepositedToAave (potential accounting issue)"*.

---

### Finding Description

`LRTWithdrawalManager` declares `totalETHDepositedToAave` as a state variable to track the ETH principal deposited into Aave: [1](#0-0) 

Interest accrual is computed in `getAccruedInterest()` as the difference between the live Aave balance and this tracker: [2](#0-1) 

The developer comment at line 642 explicitly flags the accounting risk: [3](#0-2) 

`_withdrawFromAave` is called in at least three production paths without any decrement of `totalETHDepositedToAave`:

1. **User withdrawal completion** — `_processWithdrawalCompletion` calls `_withdrawFromAave(amountNeeded)` when the contract's ETH balance is insufficient to pay a user: [4](#0-3) 

2. **Disabling Aave integration** — `setAaveIntegrationEnabled(false)` calls `_withdrawFromAave(aaveBalance)`: [5](#0-4) 

3. **Emergency withdrawal** — `emergencyWithdrawFromAave` calls `_withdrawFromAave(amount)`: [6](#0-5) 

After any of these withdrawals, `totalETHDepositedToAave` remains at its pre-withdrawal value while the actual Aave balance is reduced. The condition `aaveBalance <= totalETHDepositedToAave` becomes true, causing `getAccruedInterest()` to return 0 regardless of how much interest Aave has actually generated.

The `collectInterestToTreasury()` function, which is the only mechanism to harvest Aave yield to the protocol treasury, depends entirely on `getAccruedInterest()`: [7](#0-6) 

---

### Impact Explanation

**High — Permanent freezing of unclaimed yield.**

Once any ETH is withdrawn from Aave (which happens automatically during every user withdrawal completion when the contract's ETH balance is insufficient), `totalETHDepositedToAave` becomes permanently overstated. All subsequent calls to `collectInterestToTreasury()` will compute zero interest and transfer nothing to the treasury. The Aave yield earned on the ETH buffer — intended to benefit the protocol — is permanently inaccessible through the normal collection path. The magnitude scales with the size of the Aave buffer and the duration of operation.

---

### Likelihood Explanation

**High.** The trigger is the normal user withdrawal completion flow (`completeWithdrawal` / `completeWithdrawalForUser`). Whenever the contract's idle ETH balance is less than a user's withdrawal amount and Aave integration is enabled, `_withdrawFromAave` is called automatically. This is an expected, routine operational condition — not an edge case. Any unprivileged user completing a withdrawal can trigger it. [8](#0-7) 

---

### Recommendation

Decrement `totalETHDepositedToAave` inside `_withdrawFromAave` by the amount actually withdrawn (capped at `totalETHDepositedToAave` to avoid underflow in edge cases such as Aave slashing):

```solidity
// Inside _withdrawFromAave, after the actual withdrawal:
uint256 principalReduction = withdrawnAmount < totalETHDepositedToAave
    ? withdrawnAmount
    : totalETHDepositedToAave;
totalETHDepositedToAave -= principalReduction;
```

This mirrors the fix applied in the referenced BakerFi report: the withdrawal function must update the principal tracker so that subsequent balance-change calculations remain accurate.

---

### Proof of Concept

**State before any withdrawal:**
- `totalETHDepositedToAave` = 100 ETH (deposited via `_depositToAave`)
- Aave balance = 101 ETH (1 ETH interest accrued)
- `getAccruedInterest()` = 101 − 100 = **1 ETH** ✓

**After a user completes a withdrawal that triggers `_withdrawFromAave(50 ETH)`:**
- `totalETHDepositedToAave` = **still 100 ETH** (not decremented)
- Aave balance = 51 ETH
- `getAccruedInterest()`: `51 <= 100` → returns **0** ✗

**Result:** `collectInterestToTreasury()` transfers 0 ETH to the treasury. The 1 ETH of accrued interest (and all future interest) is permanently frozen — it cannot be collected via the normal path. [2](#0-1) [9](#0-8)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L65-65)
```text
    uint256 public totalETHDepositedToAave;
```

**File:** contracts/LRTWithdrawalManager.sol (L183-184)
```text
    function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
        _processWithdrawalCompletion(asset, msg.sender, referralId);
```

**File:** contracts/LRTWithdrawalManager.sol (L493-496)
```text
                aaveBalance = aaveAWETH.balanceOf(address(this));
                if (aaveBalance > 0) {
                    _withdrawFromAave(aaveBalance);
                }
```

**File:** contracts/LRTWithdrawalManager.sol (L540-544)
```text
    function collectInterestToTreasury() external nonReentrant onlyLRTOperator returns (uint256 interestAmount) {
        // Check health and revert if integration not enabled or unhealthy
        if (!_checkAaveHealth()) revert AaveHealthCheckFailed();

        return _collectInterestToTreasury();
```

**File:** contracts/LRTWithdrawalManager.sol (L558-562)
```text
        _collectInterestToTreasury();

        uint256 withdrawnAmount = _withdrawFromAave(amount);

        emit EmergencyWithdrawFromAave(withdrawnAmount, address(this));
```

**File:** contracts/LRTWithdrawalManager.sol (L641-646)
```text
    /// @return interest The interest amount (aWETH balance - deposited principal)
    /// @dev Returns 0 if aaveBalance < totalETHDepositedToAave (potential accounting issue)
    function getAccruedInterest() external view returns (uint256 interest) {
        uint256 aaveBalance = getAaveBalance();
        if (aaveBalance <= totalETHDepositedToAave) return 0;
        return aaveBalance - totalETHDepositedToAave;
```

**File:** contracts/LRTWithdrawalManager.sol (L720-731)
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
```
