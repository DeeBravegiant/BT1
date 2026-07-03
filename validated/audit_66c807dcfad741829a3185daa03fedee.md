### Title
Upside Price Threshold Check in `_updateRsETHPrice()` Is Relative to Last Update, Not a Fixed Benchmark — (File: contracts/LRTOracle.sol)

---

### Summary

`LRTOracle._updateRsETHPrice()` enforces a `pricePercentageLimit` guard against abnormal rsETH price increases by comparing `newRsETHPrice` against `highestRsethPrice`. However, `highestRsethPrice` is itself updated to the new price at the end of every successful call. This means the guard measures deviation only from the *previous call*, not from any stable benchmark, allowing the rsETH price to drift arbitrarily far upward in small increments without ever triggering `PriceAboveDailyThreshold`.

---

### Finding Description

In `_updateRsETHPrice()`, the upside check is:

```solidity
if (newRsETHPrice > highestRsethPrice) {
    uint256 priceDifference = newRsETHPrice - highestRsethPrice;
    bool isPriceIncreaseOffLimit =
        pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

    if (isPriceIncreaseOffLimit) {
        if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
            revert PriceAboveDailyThreshold();
        }
    }
}
// ...
if (newRsETHPrice > highestRsethPrice) {
    highestRsethPrice = newRsETHPrice;   // ← benchmark is overwritten every call
}
``` [1](#0-0) 

`highestRsethPrice` is the sole reference point for the upside guard, and it is unconditionally overwritten with `newRsETHPrice` at the end of every successful execution. Consequently, the check never accumulates deviation across multiple calls; it only ever sees the incremental change since the immediately preceding call.

This is structurally identical to the ConicEthPool pattern: the cached value (`highestRsethPrice` here, the cached token price there) is refreshed on every update, so the deviation check is always relative to the last update rather than a true fixed benchmark.

`updateRSETHPrice()` is declared `public whenNotPaused`, meaning any unprivileged caller can invoke it at will. [2](#0-1) 

---

### Impact Explanation

The `pricePercentageLimit` / `PriceAboveDailyThreshold` mechanism is the protocol's primary on-chain guard against an abnormal upward rsETH price spike (e.g., caused by a misconfigured or manipulated asset oracle, a bug in TVL accounting, or an unexpected EigenLayer event). Because `highestRsethPrice` is reset on every call, the guard is completely ineffective at detecting cumulative upward drift: a price that rises 9 % per call (with a 10 % limit) will never revert, even after 10 consecutive calls that collectively represent a ~136 % total increase. The safety invariant the contract advertises — that non-manager callers cannot push the price above the threshold — is silently broken for any multi-step increase. This constitutes a failure to deliver the promised protection without a direct loss of funds in the absence of a concurrent oracle manipulation.

**Impact class:** Low — Contract fails to deliver promised returns / safety guarantee, but does not by itself cause fund loss.

---

### Likelihood Explanation

`updateRSETHPrice()` is permissionlessly callable by any address. No special role, deposit, or economic position is required. Any actor who observes that the rsETH price has risen by just under `pricePercentageLimit` can immediately call `updateRSETHPrice()` to lock in the new `highestRsethPrice`, resetting the window. This can be repeated in the same block or across blocks. The precondition (price rising in sub-threshold increments) occurs naturally during normal protocol operation (staking rewards accumulate continuously), so the bypass is not merely theoretical — it is the default behavior of the system.

---

### Recommendation

`highestRsethPrice` should not be updated on every call. Instead, the upside guard should compare against a value that is anchored to a fixed time window (e.g., the price at the start of the current 24-hour period, analogous to how `feePeriodStartTime` already tracks the fee window). A `periodStartPrice` variable, reset once per day alongside `currentPeriodMintedFeeAmount`, would make the check measure the true cumulative daily price increase rather than the per-call increment.

---

### Proof of Concept

Assume `highestRsethPrice = 1.000 ETH` and `pricePercentageLimit = 10 %` (i.e., `1e17`).

| Call | TVL-derived price | `priceDifference` vs `highestRsethPrice` | Check result | `highestRsethPrice` after |
|------|-------------------|------------------------------------------|--------------|--------------------------|
| 1 | 1.090 ETH | 9 % < 10 % | passes | 1.090 ETH |
| 2 | 1.188 ETH | 9 % < 10 % | passes | 1.188 ETH |
| 3 | 1.295 ETH | 9 % < 10 % | passes | 1.295 ETH |
| … | … | always < 10 % | always passes | always updated |

After 8 calls the price has reached ~1.99 ETH — a 99 % increase — yet `PriceAboveDailyThreshold` was never triggered and no manager approval was ever required. The guard provided zero protection. [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L252-296)
```text
        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
        }

        // downside protection — pause if price drops too far
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }

            // if price has decreased compared to the previous price, emit an event to reflect that
            if (previousPrice > newRsETHPrice) {
                emit RsETHPriceDecrease(newRsETHPrice, previousPrice);
            }

            // emit an event to notify that the price is currently below the peak (all time high) price
            emit RsETHPriceBelowPeak(highestRsethPrice, newRsETHPrice);
        }

        // update highest price if new price exceeds it
        if (newRsETHPrice > highestRsethPrice) {
            highestRsethPrice = newRsETHPrice;
        }
```
