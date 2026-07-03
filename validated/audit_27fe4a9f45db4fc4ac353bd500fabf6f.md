Audit Report

## Title
`LRTOracle._updateRsETHPrice` Decrease Safety Check Measures Cumulative ATH Deviation Instead of Current Price Movement, Enabling Spurious Protocol Pause - (File: contracts/LRTOracle.sol)

## Summary
The decrease branch of `_updateRsETHPrice()` computes `diff = highestRsethPrice - newRsETHPrice`, measuring the total cumulative drop from the all-time high rather than the incremental change from the last stored price. Because `updateRSETHPrice()` is a public function callable by any address, any caller can trigger a full protocol pause when the cumulative ATH deviation crosses `pricePercentageLimit`, even if the current price movement is negligible. This temporarily freezes all user deposits and withdrawals.

## Finding Description
`updateRSETHPrice()` at line 87 is `public` with no access control beyond `whenNotPaused`. It delegates to `_updateRsETHPrice()`.

The increase check (lines 252–267) only activates when `newRsETHPrice > highestRsethPrice` and measures the incremental step above the previous ATH:
```solidity
uint256 priceDifference = newRsETHPrice - highestRsethPrice;
bool isPriceIncreaseOffLimit =
    pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```

The decrease check (lines 270–282) activates whenever `newRsETHPrice < highestRsethPrice` and measures the **total cumulative drop from ATH**, not from `previousPrice`:
```solidity
uint256 diff = highestRsethPrice - newRsETHPrice;
bool isPriceDecreaseOffLimit =
    pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);
```

When `isPriceDecreaseOffLimit` is true, the function pauses `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle` itself (lines 278–281), then returns without updating `rsETHPrice`.

The asymmetry: the increase check is incremental (ATH-to-new-ATH step), while the decrease check is cumulative (ATH-to-current absolute gap). The same `pricePercentageLimit` value therefore produces fundamentally different sensitivity depending on direction. A price that has been 9% below ATH for weeks will pause the protocol on any further drop of just over 1%, while a single-step 9% drop from ATH itself does not trigger a pause.

No oracle manipulation or privileged access is required. The attacker only needs to call the public `updateRSETHPrice()` at the moment the cumulative ATH gap crosses the threshold.

## Impact Explanation
When the pause triggers, `LRTDepositPool` and `LRTWithdrawalManager` are both paused, freezing all user deposits and withdrawals until an admin calls `unpause()`. This is a **temporary freezing of funds**, a Medium-severity impact in the allowed scope. The freeze is not permanent because an admin can unpause, but it is user-harmful and can be triggered repeatedly by any unprivileged caller whenever market conditions place the price near the threshold.

## Likelihood Explanation
- `updateRSETHPrice()` is unconditionally public; any EOA or contract can call it.
- The scenario arises naturally in any market where rsETH price has declined from its ATH over time (e.g., slashing events, redemption pressure, or yield accrual pauses). No special setup is needed.
- Once the cumulative gap is near the threshold, the attacker simply waits for a routine price update that pushes it over, or calls the function themselves to confirm the trigger.
- The condition is repeatable: after an admin unpauses, if the price remains below the threshold, the next public call to `updateRSETHPrice()` re-triggers the pause.

## Recommendation
Replace the ATH reference in the decrease check with `previousPrice` so both checks measure incremental movement symmetrically:

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

This ensures the same `pricePercentageLimit` threshold governs both upward and downward single-step movements, eliminating the cumulative-deviation false trigger.

## Proof of Concept
1. Admin sets `pricePercentageLimit = 0.1e18` (10%).
2. State: `highestRsethPrice = 1.2e18`, `rsETHPrice = 1.09e18` (9.2% below ATH — within limit, protocol is live).
3. Any user calls `updateRSETHPrice()`. The oracle computes `newRsETHPrice = 1.07e18` (a 1.8% drop from the current stored price).
4. Decrease check evaluates:
   - `diff = 1.2e18 − 1.07e18 = 0.13e18`
   - `limit = 0.1 × 1.2e18 = 0.12e18`
   - `0.13e18 > 0.12e18` → `isPriceDecreaseOffLimit = true`
5. `LRTDepositPool.pause()`, `LRTWithdrawalManager.pause()`, and `LRTOracle._pause()` are called.
6. All user deposits and withdrawals are frozen. The actual price movement was 1.8%, well within the intended 10% threshold.
7. After admin unpauses, if the price remains below `highestRsethPrice` by more than 10%, any subsequent public call to `updateRSETHPrice()` re-triggers the pause.

**Foundry test plan**: Deploy `LRTOracle` with a mock config, set `highestRsethPrice = 1.2e18`, `rsETHPrice = 1.09e18`, `pricePercentageLimit = 0.1e18`. Mock `_getTotalEthInProtocol()` to return a value yielding `newRsETHPrice = 1.07e18`. Call `updateRSETHPrice()` from an unprivileged address. Assert `lrtDepositPool.paused() == true`, `withdrawalManager.paused() == true`, and `lrtOracle.paused == true`.