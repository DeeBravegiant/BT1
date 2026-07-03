Looking at the exact code paths in `contracts/LRTWithdrawalManager.sol`:

### Title
`emergencyWithdrawFromAave` Silently Withdraws Less Than Requested Amount Without Reverting - (`contracts/LRTWithdrawalManager.sol`)

### Summary

`emergencyWithdrawFromAave` can complete successfully while withdrawing less ETH than the caller requested, emitting a misleading `EmergencyWithdrawFromAave` event and leaving `totalETHDepositedToAave` in a stale state.

### Finding Description

The execution path in `emergencyWithdrawFromAave` is:

1. Check `aaveBalance != 0` (line 555) — passes as long as any aWETH exists
2. Call `_collectInterestToTreasury()` (line 958) — silently returns 0 when `aaveBalance <= totalETHDepositedToAave`
3. Call `_withdrawFromAave(amount)` (line 560) — caps the actual withdrawal at `withdrawablePrincipal` [1](#0-0) 

`_collectInterestToTreasury` returns 0 without reverting when `aaveBalance <= principal`. This is by design for normal rounding, but it means the subsequent `_withdrawFromAave` call operates on a balance that may be less than `totalETHDepositedToAave`. [2](#0-1) 

`_withdrawFromAave` computes `withdrawablePrincipal = min(aaveBalance, totalETHDepositedToAave)` and silently caps `withdrawnAmount` at that value. There is no revert if `withdrawnAmount < amount`. [3](#0-2) 

The event then emits `withdrawnAmount` (the capped value), not `amount` (what was requested), with no revert.

**Concrete scenario:**
- `totalETHDepositedToAave = 100 ETH`, `aaveBalance = 99 ETH` (1 ETH lost to Aave rounding or slashing)
- PAUSER calls `emergencyWithdrawFromAave(100 ETH)`
- `_collectInterestToTreasury()` → returns 0 (99 ≤ 100)
- `_withdrawFromAave(100 ETH)` → `withdrawablePrincipal = 99`, `withdrawnAmount = 99`
- Emits `EmergencyWithdrawFromAave(99, address(this))` — no revert
- `totalETHDepositedToAave` becomes 1 (stale, since Aave balance is now 0)

The stale `totalETHDepositedToAave = 1` persists after all aWETH is burned, corrupting the accounting state for any future Aave deposits.

### Impact Explanation

**Low. Contract fails to deliver promised returns, but doesn't lose value.**

The emergency withdrawal function does not enforce that the full requested amount is recovered or that the transaction reverts. Operators receive a misleading event suggesting a partial recovery succeeded cleanly. Additionally, `totalETHDepositedToAave` is left with a non-zero stale value after all Aave funds are withdrawn, corrupting future deposit accounting.

### Likelihood Explanation

Aave aWETH balances can diverge from `totalETHDepositedToAave` by small amounts due to rounding in Aave's internal accounting (acknowledged in `_checkAaveHealth` which allows up to 2 wei difference). A more significant divergence requires an Aave-level slashing event. The rounding case is low-impact (wei-level); the slashing case is low-probability but higher-impact. The PAUSER_ROLE is a legitimate, non-compromised caller. [4](#0-3) 

### Recommendation

In `emergencyWithdrawFromAave`, after calling `_withdrawFromAave`, validate that the withdrawn amount meets expectations or revert:

```solidity
uint256 withdrawnAmount = _withdrawFromAave(amount);
// If a specific amount was requested (not max), enforce it was fully withdrawn
if (amount != type(uint256).max && withdrawnAmount < amount) {
    revert InsufficientAaveBalance();
}
emit EmergencyWithdrawFromAave(withdrawnAmount, address(this));
```

Alternatively, when `amount == type(uint256).max`, pass `aaveBalance` (re-read after interest collection) directly to `_withdrawFromAave` to ensure all available funds are withdrawn.

### Proof of Concept

```solidity
// Mock aaveAWETH.balanceOf to return totalETHDepositedToAave - 1
// e.g., totalETHDepositedToAave = 100e18, aaveBalance = 100e18 - 1

// Call emergencyWithdrawFromAave(100e18) as PAUSER_ROLE
// _collectInterestToTreasury() returns 0 (no revert)
// _withdrawFromAave(100e18):
//   withdrawablePrincipal = min(100e18 - 1, 100e18) = 100e18 - 1
//   withdrawnAmount = min(100e18, 100e18 - 1) = 100e18 - 1
// EmergencyWithdrawFromAave emitted with amount = 100e18 - 1
// totalETHDepositedToAave = 1 (stale, Aave balance = 0)
// No revert — assert withdrawnAmount < amount passes
```

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L560-562)
```text
        uint256 withdrawnAmount = _withdrawFromAave(amount);

        emit EmergencyWithdrawFromAave(withdrawnAmount, address(this));
```

**File:** contracts/LRTWithdrawalManager.sol (L911-915)
```text
        // Only withdraw up to the principal amount (don't use accrued interest for user withdrawals)
        uint256 withdrawablePrincipal = aaveBalance < totalETHDepositedToAave ? aaveBalance : totalETHDepositedToAave;

        withdrawnAmount = amount > withdrawablePrincipal ? withdrawablePrincipal : amount;
        if (withdrawnAmount == 0) return 0;
```

**File:** contracts/LRTWithdrawalManager.sol (L929-932)
```text
        // Allow small rounding differences (up to 2 wei)
        // Check if balance is significantly less than principal
        if (principal > aaveBalance && principal - aaveBalance > 2) return false;
        return true;
```

**File:** contracts/LRTWithdrawalManager.sol (L945-950)
```text
    function _collectInterestToTreasury() internal returns (uint256 interestAmount) {
        uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
        uint256 principal = totalETHDepositedToAave;

        // Return 0 if no interest or balance is less than principal (accounting for rounding)
        if (aaveBalance <= principal) return 0;
```
