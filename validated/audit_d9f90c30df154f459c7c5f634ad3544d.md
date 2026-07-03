Audit Report

## Title
`LRTOracle._updateRsETHPrice` Decrease Safety Check Measures Cumulative ATH Deviation Instead of Current Price Movement, Causing Unintended Protocol Pause - (File: contracts/LRTOracle.sol)

## Summary
The decrease branch of the `pricePercentageLimit` safety check in `_updateRsETHPrice()` computes deviation relative to `highestRsethPrice` (the all-time high), not the previously stored `rsETHPrice`. This means the protocol can be paused when the current price movement is well within the intended threshold, simply because the cumulative drop from ATH has crossed it. The pause freezes all user deposits and withdrawals, and the trigger function is public.

## Finding Description
In `_updateRsETHPrice()`, the increase check fires only when `newRsETHPrice > highestRsethPrice` and measures the incremental step above the previous ATH:

```solidity
// lines 252–257
uint256 priceDifference = newRsETHPrice - highestRsethPrice;
bool isPriceIncreaseOffLimit =
    pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
``` [1](#0-0) 

The decrease check fires whenever `newRsETHPrice < highestRsethPrice` and measures the **total cumulative drop from ATH**:

```solidity
// lines 270–274
uint256 diff = highestRsethPrice - newRsETHPrice;
bool isPriceDecreaseOffLimit =
    pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);
``` [2](#0-1) 

`previousPrice` is captured at line 228 but is never used in the decrease check. [3](#0-2) 

When `isPriceDecreaseOffLimit` is true, the function pauses `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle` itself and returns without updating the price: [4](#0-3) 

The trigger function is public and callable by any address: [5](#0-4) 

**Concrete scenario with `pricePercentageLimit = 0.1e18` (10%):**
- `highestRsethPrice = 1.2e18`, `rsETHPrice = 1.09e18` (9.2% below ATH — within limit, protocol running normally)
- Routine price update yields `newRsETHPrice = 1.07e18` (1.8% drop from current price)
- `diff = 1.2e18 − 1.07e18 = 0.13e18`; `limit = 0.1 × 1.2e18 = 0.12e18`
- `0.13e18 > 0.12e18` → `isPriceDecreaseOffLimit = true` → protocol paused
- Actual current movement was 1.8%, far below the intended 10% threshold

No existing guard prevents this: the check itself is the guard, and it is structurally incorrect.

## Impact Explanation
When the pause triggers, `LRTDepositPool` and `LRTWithdrawalManager` are both paused, freezing all user deposits and withdrawals until an admin manually unpauses. This is a concrete **temporary freezing of funds** (Medium severity per the allowed impact scope). The freeze is not permanent because an admin can unpause, but it is unintended and can recur naturally without any privileged action by an attacker.

## Likelihood Explanation
`updateRSETHPrice()` is a public function with no access control. No oracle manipulation, privileged access, or victim mistake is required. The scenario arises from ordinary market conditions: if the price has been below ATH for any period (e.g., 9% below ATH), a small further drop (e.g., 1.1%) crosses the 10% cumulative threshold and triggers the pause. Any caller — including routine keepers or unprivileged users — invoking `updateRSETHPrice()` at that moment causes the freeze. The condition is repeatable: after each admin unpause, the same cumulative-ATH check remains, and the next small price dip below the threshold re-triggers it.

## Recommendation
Replace the ATH reference in the decrease check with `previousPrice` (the last stored `rsETHPrice`), making it symmetric with the increase check (both measure incremental movement):

```solidity
// Recommended:
if (newRsETHPrice < previousPrice) {
    uint256 diff = previousPrice - newRsETHPrice;
    bool isPriceDecreaseOffLimit =
        pricePercentageLimit > 0 && previousPrice > 0
        && diff > pricePercentageLimit.mulWad(previousPrice);
    ...
}
```

This ensures the same `pricePercentageLimit` value governs the same magnitude of movement in both directions, and prevents pausing due to accumulated historical deviation from ATH.

## Proof of Concept
Foundry fork/unit test plan:
1. Deploy `LRTOracle` with a mock `LRTConfig`, `LRTDepositPool`, and `LRTWithdrawalManager`.
2. Set `pricePercentageLimit = 0.1e18`.
3. Seed state: `highestRsethPrice = 1.2e18`, `rsETHPrice = 1.09e18` (simulate by calling `updateRSETHPrice()` with mocked oracle returning 1.09 after ATH was 1.2).
4. Mock the asset oracle to return a value that yields `newRsETHPrice = 1.07e18`.
5. Call `updateRSETHPrice()` from an unprivileged EOA.
6. Assert `lrtDepositPool.paused() == true`, `withdrawalManager.paused() == true`, `lrtOracle.paused() == true`.
7. Assert the actual price movement was `(1.09 − 1.07) / 1.09 ≈ 1.8%`, confirming the pause fired well below the 10% intended threshold.

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L228-228)
```text
        uint256 previousPrice = rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L252-257)
```text
        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```

**File:** contracts/LRTOracle.sol (L270-274)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);
```

**File:** contracts/LRTOracle.sol (L277-282)
```text
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }
```
