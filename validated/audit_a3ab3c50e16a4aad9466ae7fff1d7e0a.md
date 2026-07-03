The code confirms the described behavior. Let me verify the exact lines cited before rendering a verdict.

The code at `contracts/LRTOracle.sol` lines 252–266 confirms the revert path:
- Non-manager callers revert with `PriceAboveDailyThreshold` when `isPriceIncreaseOffLimit` is true.
- `rsETHPrice = newRsETHPrice` at line 313 is never reached in that case.

`contracts/LRTDepositPool.sol` line 520 confirms `rsETHPrice()` is used as the denominator in `getRsETHAmountToMint`, and deposits are **not** paused when the price update reverts — `LRTDepositPool` remains open.

The critical design gap: when the price threshold blocks public updates, the protocol does **not** pause `LRTDepositPool`. Deposits continue against a stale (lower) `rsETHPrice`, minting excess rsETH to new depositors and permanently diluting existing holders' proportional claim. No attacker action is needed; the condition arises from normal EigenLayer reward accrual. The manager escape hatch (`updateRSETHPriceAsManager`) is the only remedy, but its latency is unbounded.

---

Audit Report

## Title
Stale `rsETHPrice` During Price-Threshold Revert Allows New Depositors to Mint Excess rsETH, Diluting Existing Holders' Yield - (`contracts/LRTOracle.sol`)

## Summary
When `_updateRsETHPrice()` computes a `newRsETHPrice` that exceeds `highestRsethPrice` by more than `pricePercentageLimit`, it reverts for any non-manager caller, leaving `rsETHPrice` at its previous stale value. Because `LRTDepositPool` is not paused in this scenario, deposits continue to use the stale (lower) `rsETHPrice` as the denominator in `getRsETHAmountToMint`, minting more rsETH than the true NAV justifies and permanently diluting existing holders' yield.

## Finding Description
`LRTOracle._updateRsETHPrice()` contains a threshold guard at lines 252–266:

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
``` [1](#0-0) 

When this revert fires, execution never reaches line 313 (`rsETHPrice = newRsETHPrice`), so the stored price remains stale. [2](#0-1) 

The public entry point `updateRSETHPrice()` (lines 87–89) is callable by anyone but provides no fallback — it simply calls `_updateRsETHPrice()` and reverts. [3](#0-2) 

Meanwhile, `LRTDepositPool.getRsETHAmountToMint()` uses `lrtOracle.rsETHPrice()` as the denominator:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [4](#0-3) 

A stale (lower) `rsETHPrice` inflates `rsethAmountToMint`. `LRTDepositPool` is not paused when the oracle update reverts, so deposits proceed unimpeded against the stale rate. The only remedy is the manager calling `updateRSETHPriceAsManager()` (lines 94–96), but its latency is unbounded. [5](#0-4) 

## Impact Explanation
**High — Theft of unclaimed yield.** Every deposit made while `rsETHPrice` is stale mints rsETH at a rate below the true NAV. New depositors receive a larger fractional share of the pool than they are entitled to. When the price is eventually corrected, existing holders' proportional claim on the underlying ETH is permanently reduced. The magnitude scales with deposit volume and the duration of the stale window.

## Likelihood Explanation
**Medium.** The trigger is a normal market event: EigenLayer rewards accruing faster than `pricePercentageLimit` allows in one update cycle. With a conservative limit (e.g., 1% = `1e16`), any day with above-average validator rewards or a large batch of EigenLayer reward distributions can exceed it. No attacker action is required. The stale window persists until the MANAGER manually intervenes; there is no on-chain staleness check or automatic deposit pause.

## Recommendation
1. **Do not revert for public callers.** Instead, cap the price update at `highestRsethPrice + pricePercentageLimit.mulWad(highestRsethPrice)` and emit an event, allowing the price to advance incrementally each call until it converges to the true value.
2. Alternatively, if the revert is intentional, **pause `LRTDepositPool`** atomically when `PriceAboveDailyThreshold` would be triggered for a public caller, preventing deposits against the stale price until the manager acts.
3. At minimum, add an on-chain staleness check in `getRsETHAmountToMint()` that reverts if `rsETHPrice` has not been updated within an acceptable window.

## Proof of Concept
1. `highestRsethPrice = 1.05 ether`, `pricePercentageLimit = 1e16` (1%).
2. EigenLayer rewards accrue; `_getTotalEthInProtocol()` implies `newRsETHPrice = 1.062 ether` (1.14% increase).
3. Any EOA calls `updateRSETHPrice()`:
   - `priceDifference = 0.012e18`
   - `pricePercentageLimit.mulWad(highestRsethPrice) = 1.05e16`
   - `0.012e18 > 1.05e16` → `isPriceIncreaseOffLimit = true`
   - Caller is not MANAGER → `revert PriceAboveDailyThreshold()`
4. `rsETHPrice` remains `1.05 ether`.
5. Depositor calls `depositETH{value: 10 ether}(0, "")`:
   - `getRsETHAmountToMint` returns `10e18 * 1e18 / 1.05e18 ≈ 9.524 rsETH`
   - Correct amount: `10e18 / 1.062e18 ≈ 9.416 rsETH`
   - Excess: ~0.108 rsETH, diluting all existing holders.
6. Repeats for every deposit until MANAGER calls `updateRSETHPriceAsManager()`.

**Foundry test plan:** Fork mainnet, set `pricePercentageLimit = 1e16`, manipulate `_getTotalEthInProtocol()` return to exceed the threshold, call `updateRSETHPrice()` as a non-manager EOA (expect revert), then call `depositETH` and assert `rsethAmountToMint > (depositAmount * 1e18 / truePrice)`.

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

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
