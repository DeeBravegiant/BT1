The code confirms the claim. Let me verify the key logic:

The code confirms the claim exactly. The asymmetry is real and the PoC arithmetic checks out.

Audit Report

## Title
`pricePercentageLimit` Upside Guard Bypassed During Sub-Peak Price Recovery - (File: contracts/LRTOracle.sol)

## Summary
`LRTOracle._updateRsETHPrice()` enforces `pricePercentageLimit` on upward price moves only when `newRsETHPrice > highestRsethPrice`. When the price is recovering from a prior dip and remains below `highestRsethPrice`, the entire upside guard is skipped regardless of how large the per-update increase is. A single call to the permissionless `updateRSETHPrice()` can commit an arbitrarily large upward price move, causing depositors in that block to receive fewer rsETH tokens than the protocol's safety model intends.

## Finding Description
`_updateRsETHPrice()` computes `newRsETHPrice` and then runs two conditional branches:

**Upside branch** — gated on `newRsETHPrice > highestRsethPrice`: [1](#0-0) 

**Downside branch** — entered whenever `newRsETHPrice < highestRsethPrice`: [2](#0-1) 

When the price is in the recovery range `(previousPrice, highestRsethPrice]`, neither branch enforces the per-update cap: the upside branch condition is false (new price does not exceed the all-time high), and the downside branch measures `diff = highestRsethPrice - newRsETHPrice`, which shrinks as the new price approaches the peak. A price that jumps 9.9% from `previousPrice` to just below `highestRsethPrice` produces a tiny `diff` that falls under the threshold, so no pause fires and no revert occurs. The price is then committed unconditionally: [3](#0-2) 

`updateRSETHPrice()` is permissionless (`public whenNotPaused`): [4](#0-3) 

## Impact Explanation
**Low — Contract fails to deliver promised returns.**

`pricePercentageLimit` is documented and configured as a per-update price-change guard. When it fails to fire during sub-peak recovery, `rsETHPrice` is set to an anomalously high value. `LRTDepositPool.getRsETHAmountToMint()` divides by `rsETHPrice`, so depositors in the same block receive proportionally fewer rsETH tokens than the protocol's safety model guarantees: [5](#0-4) 

## Likelihood Explanation
**Medium.** A price below `highestRsethPrice` followed by recovery is a routine operating condition (post-slashing resolution, temporary oracle dip). Any external account can call `updateRSETHPrice()` at any time to trigger the update. No privileged role or special setup is required beyond the price being in the recovery range.

## Recommendation
Remove the `if (newRsETHPrice > highestRsethPrice)` gate and instead measure the per-update increase relative to `previousPrice` symmetrically with the downside check. For example:

```solidity
if (newRsETHPrice > previousPrice) {
    uint256 priceDifference = newRsETHPrice - previousPrice;
    bool isPriceIncreaseOffLimit =
        pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(previousPrice);
    if (isPriceIncreaseOffLimit) {
        if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
            revert PriceAboveDailyThreshold();
        }
    }
}
```

If the intent is specifically to cap only new all-time-high excursions, document this explicitly and add a separate per-update increase guard relative to `previousPrice` for the recovery range.

## Proof of Concept
**State:** `highestRsethPrice = 1.10e18`, `rsETHPrice = 1.00e18`, `pricePercentageLimit = 1e16` (1%).

**Action:** Any account calls `updateRSETHPrice()` when `_getTotalEthInProtocol()` yields a `newRsETHPrice = 1.099e18` (9.9% increase from `previousPrice`).

**Trace through `_updateRsETHPrice()`:**
1. Upside branch: `1.099e18 > 1.10e18` → **false** → entire upside guard skipped.
2. Downside branch: `1.099e18 < 1.10e18` → **true** → `diff = 1.10e18 − 1.099e18 = 1e15`; threshold = `1e16 × 1.10 = 1.10e16`; `1e15 > 1.10e16` → **false** → no pause, no revert.
3. `rsETHPrice` is set to `1.099e18` — a 9.9% single-block increase — bypassing the 1% `pricePercentageLimit` entirely.

**Foundry test plan:** Deploy `LRTOracle` with a mock config; set `highestRsethPrice = 1.10e18`, `rsETHPrice = 1.00e18`, `pricePercentageLimit = 1e16`; mock `_getTotalEthInProtocol()` to return a value yielding `newRsETHPrice = 1.099e18`; call `updateRSETHPrice()` from an unprivileged address; assert `rsETHPrice == 1.099e18` and no revert/pause occurred, confirming the guard was not applied.

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L252-267)
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
```

**File:** contracts/LRTOracle.sol (L270-282)
```text
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
```

**File:** contracts/LRTOracle.sol (L313-315)
```text
        rsETHPrice = newRsETHPrice;

        emit RsETHPriceUpdate(rsETHPrice, previousPrice);
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
