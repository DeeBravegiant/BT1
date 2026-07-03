### Title
`pricePercentageLimit` Downside Check Measured Against All-Time-High Causes Spurious Protocol-Wide Pause — (File: `contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle._updateRsETHPrice()` uses a single `pricePercentageLimit` value as both an upside guard and a downside circuit-breaker. The downside check compares the new price against `highestRsethPrice` — the **all-time high** — rather than the previous price. Any temporary dip from the ATH that exceeds `pricePercentageLimit` triggers an automatic, irreversible (until admin action) pause of the deposit pool, withdrawal manager, and oracle. Because `updateRSETHPrice()` is `public`, any unprivileged caller can trigger this freeze whenever normal oracle fluctuations in underlying LST prices cause the computed rsETH price to fall below the ATH by more than the configured threshold.

---

### Finding Description

`_updateRsETHPrice()` in `LRTOracle.sol` implements two price-deviation guards using the same `pricePercentageLimit` variable:

**Upside guard** (lines 252–267): if `newRsETHPrice > highestRsethPrice` and the increase exceeds `pricePercentageLimit * highestRsethPrice`, non-manager callers get a revert.

**Downside guard** (lines 270–282):
```solidity
if (newRsETHPrice < highestRsethPrice) {
    uint256 diff = highestRsethPrice - newRsETHPrice;
    bool isPriceDecreaseOffLimit =
        pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

    if (isPriceDecreaseOffLimit) {
        if (!lrtDepositPool.paused()) lrtDepositPool.pause();
        if (!withdrawalManager.paused()) withdrawalManager.pause();
        _pause();
        return;
    }
}
```

The baseline for the downside check is `highestRsethPrice` — the **all-time high** — not `previousPrice`. rsETH is an LRT that accrues staking rewards, so its price is expected to grow monotonically over time. However, the underlying asset prices (stETH, cbETH, etc.) are fetched from external oracles and can fluctuate slightly between blocks. A small downward tick in any underlying oracle price can cause the computed rsETH price to fall below `highestRsethPrice` by more than `pricePercentageLimit`, even though no real loss has occurred.

The entry point is fully public:
```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

Any unprivileged user can call `updateRSETHPrice()`. If the oracle-computed price happens to be below `highestRsethPrice` by more than `pricePercentageLimit` at that moment, the call atomically pauses `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle` itself. Unpausing requires `onlyLRTAdmin` on each contract separately.

---

### Impact Explanation

**Temporary freezing of funds.** When the pause is triggered:
- All deposits via `LRTDepositPool` are blocked (the contract is `PausableUpgradeable` and guards its deposit functions with `whenNotPaused`).
- All withdrawals via `LRTWithdrawalManager` are blocked.
- `LRTOracle.updateRSETHPrice()` itself becomes blocked (`whenNotPaused`), so the price cannot be updated by anyone except the manager via `updateRSETHPriceAsManager()`.

Users cannot deposit or withdraw until an admin manually unpauses all three contracts. This matches the **Medium — Temporary freezing of funds** impact category.

---

### Likelihood Explanation

The likelihood is **medium-high**:

1. `pricePercentageLimit` is a single value shared for both upside and downside checks. A tight value (e.g., 1% = `1e16`) is reasonable for the upside guard but structurally too tight for the downside guard because the downside baseline is the ATH, not the last price.
2. rsETH's computed price depends on the sum of `totalAssetAmt * assetPrice` across all supported LSTs. Any one of those oracle prices dipping slightly (e.g., stETH/ETH Chainlink feed ticking down by 0.5% intraday) can push the computed rsETH price below `highestRsethPrice` by more than `pricePercentageLimit`.
3. `updateRSETHPrice()` is callable by anyone, so no privileged access is needed to trigger the freeze — it can happen on any routine price update call.
4. The protocol is live on mainnet with real user funds, and LST oracle prices fluctuate daily.

---

### Recommendation

Separate the downside circuit-breaker baseline from the all-time high. Compare the downside deviation against `previousPrice` (the last stored `rsETHPrice`) rather than `highestRsethPrice`. Alternatively, use two distinct parameters: a tight `priceIncreaseLimit` for the upside guard and a wider `priceDecreaseLimit` for the downside circuit-breaker, both measured against the previous price. This mirrors the fix applied in the referenced report (widening the threshold from 0.001% to 5%).

---

### Proof of Concept

1. Protocol operates normally; rsETH price grows from `1.00 ETH` to `1.05 ETH` over several weeks. `highestRsethPrice = 1.05e18`.
2. Admin sets `pricePercentageLimit = 1e16` (1%).
3. stETH/ETH Chainlink feed ticks down 1.1% intraday (normal volatility). The computed `newRsETHPrice` becomes `~1.0385e18`.
4. `diff = 1.05e18 - 1.0385e18 = 0.0115e18`. `pricePercentageLimit.mulWad(highestRsethPrice) = 0.01 * 1.05e18 = 0.0105e18`. `diff > 0.0105e18` → `isPriceDecreaseOffLimit = true`.
5. Any user calls `updateRSETHPrice()`. The function atomically pauses `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle`.
6. All deposits and withdrawals are frozen until admin manually unpauses each contract.

**Relevant lines:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/LRTOracle.sol (L29-30)
```text
    uint256 public pricePercentageLimit;
    uint256 public highestRsethPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L125-127)
```text
    function setPricePercentageLimit(uint256 _pricePercentageLimit) external onlyLRTAdmin {
        pricePercentageLimit = _pricePercentageLimit;
        emit PricePercentageLimitUpdate(_pricePercentageLimit);
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
