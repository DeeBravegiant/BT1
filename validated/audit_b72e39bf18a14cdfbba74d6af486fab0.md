### Title
`updateRSETHPrice()` Reverts for Non-Managers When Price Increase Exceeds `pricePercentageLimit`, Causing Stale rsETH Price to Dilute Existing Holders - (`contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle._updateRsETHPrice()` reverts with `PriceAboveDailyThreshold` for any non-manager caller when the computed new rsETH price exceeds `highestRsethPrice` by more than `pricePercentageLimit`. Because the public `updateRSETHPrice()` function is the primary mechanism for keeping `rsETHPrice` current, this revert leaves the stored price stale. The stale (lower) `rsETHPrice` is then consumed directly by `LRTDepositPool.getRsETHAmountToMint()`, causing new depositors to receive more rsETH than they are entitled to, diluting the yield of existing rsETH holders.

---

### Finding Description

`LRTOracle` stores a `rsETHPrice` state variable that is the authoritative exchange rate used across the protocol. The public entry point `updateRSETHPrice()` is gated by `whenNotPaused` and calls `_updateRsETHPrice()`. [1](#0-0) 

Inside `_updateRsETHPrice()`, after computing `newRsETHPrice`, the function checks whether the price increase exceeds `pricePercentageLimit` relative to `highestRsethPrice`. If it does, and the caller is not a MANAGER, the function reverts: [2](#0-1) 

This means that whenever EigenLayer rewards cause the rsETH NAV to jump by more than `pricePercentageLimit` in a single update window, every call to the public `updateRSETHPrice()` reverts. The stored `rsETHPrice` is never written (line 313 is never reached), so it remains at its previous, lower value. [3](#0-2) 

The stale `rsETHPrice` is then used directly in `LRTDepositPool.getRsETHAmountToMint()`: [4](#0-3) 

Because `rsETHPrice` is the denominator, a stale (lower) value inflates `rsethAmountToMint`, giving new depositors more rsETH than the current NAV justifies. This is the same share-dilution mechanism that steals accrued yield from existing holders.

The manager escape hatch `updateRSETHPriceAsManager()` exists but is not callable by unprivileged users and requires timely manual intervention: [5](#0-4) 

---

### Impact Explanation

**Impact: High — Theft of unclaimed yield.**

While the price update is blocked, every deposit mints rsETH at the stale (lower) rate. New depositors receive a larger share of the pool than the true NAV warrants. When the price is eventually corrected, existing holders' proportional claim on the underlying ETH is permanently diluted. The magnitude scales with the size and duration of the stale window and the volume of deposits during that period.

---

### Likelihood Explanation

**Likelihood: Medium.**

The trigger is a legitimate market event — EigenLayer rewards accruing faster than `pricePercentageLimit` allows in one update cycle. If `pricePercentageLimit` is set conservatively (e.g., 1% = `1e16`), any day with above-average validator rewards or a large batch of EigenLayer reward distributions could exceed it. The window of staleness lasts until a MANAGER manually calls `updateRSETHPriceAsManager()`. No attacker action is required; the condition arises from normal protocol operation.

---

### Recommendation

1. **Do not revert** when the price increase exceeds the threshold for public callers. Instead, cap the price update at `highestRsethPrice + pricePercentageLimit.mulWad(highestRsethPrice)` and emit an event, allowing the price to advance incrementally each call until it converges to the true value.
2. Alternatively, allow the full price update but emit a warning event and require a manager confirmation within a time window before the new price takes effect for minting.
3. At minimum, document that `updateRSETHPriceAsManager()` must be called promptly whenever `updateRSETHPrice()` reverts, and add an on-chain staleness check in `getRsETHAmountToMint()` that reverts if `rsETHPrice` has not been updated within an acceptable window.

---

### Proof of Concept

1. Protocol is operating normally; `highestRsethPrice = 1.05 ether`, `pricePercentageLimit = 1e16` (1%).
2. A large batch of EigenLayer rewards is distributed; `_getTotalEthInProtocol()` now returns a value that implies `newRsETHPrice = 1.062 ether` (a 1.14% increase, above the 1% limit).
3. Any EOA calls `updateRSETHPrice()`. Inside `_updateRsETHPrice()`:
   - `priceDifference = 1.062e18 - 1.05e18 = 0.012e18`
   - `pricePercentageLimit.mulWad(highestRsethPrice) = 1e16 * 1.05e18 / 1e18 = 1.05e16`
   - `0.012e18 > 1.05e16` → `isPriceIncreaseOffLimit = true`
   - Caller is not MANAGER → `revert PriceAboveDailyThreshold()`
4. `rsETHPrice` remains `1.05 ether` (stale).
5. A depositor calls `LRTDepositPool.depositETH{value: 10 ether}(0, "")`.
   - `getRsETHAmountToMint` computes: `10e18 * 1e18 / 1.05e18 ≈ 9.524 rsETH` (should be `10e18 / 1.062e18 ≈ 9.416 rsETH`).
   - The depositor receives ~0.108 rsETH extra, diluting all existing holders.
6. This continues for every deposit until the MANAGER manually calls `updateRSETHPriceAsManager()`. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L94-96)
```text
    function updateRSETHPriceAsManager() external onlyLRTManager {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L252-266)
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
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
