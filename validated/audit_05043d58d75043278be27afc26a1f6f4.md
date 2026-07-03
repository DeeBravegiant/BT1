### Title
Absence of Minimum Bound on `pricePercentageLimit` Silently Disables Both Upside-Threshold and Downside Auto-Pause Protection - (File: `contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle.setPricePercentageLimit` accepts any value including `0` with no lower-bound guard. When `pricePercentageLimit` is `0`, the two `pricePercentageLimit > 0 && …` short-circuit guards inside `_updateRsETHPrice` evaluate to `false`, silently disabling (a) the upside gate that blocks non-manager callers from committing an anomalous price increase, and (b) the downside circuit-breaker that auto-pauses the entire protocol on a significant price drop. The result is that a slashing event or any large TVL decrease will no longer trigger the protective pause, allowing continued deposits at a depressed rsETH price that dilutes all existing rsETH holders.

---

### Finding Description

`LRTOracle` maintains `pricePercentageLimit` as the single knob controlling two complementary safety rails inside `_updateRsETHPrice`:

**Upside gate** (lines 256–266):
```solidity
bool isPriceIncreaseOffLimit =
    pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
if (isPriceIncreaseOffLimit) {
    if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender))
        revert PriceAboveDailyThreshold();
}
```

**Downside circuit-breaker** (lines 273–281):
```solidity
bool isPriceDecreaseOffLimit =
    pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);
if (isPriceDecreaseOffLimit) {
    if (!lrtDepositPool.paused()) lrtDepositPool.pause();
    if (!withdrawalManager.paused()) withdrawalManager.pause();
    _pause();
    return;
}
```

Both booleans are gated on `pricePercentageLimit > 0`. When the limit is `0`, both evaluate to `false` unconditionally, regardless of how large the price movement is.

The setter imposes no lower bound:

```solidity
function setPricePercentageLimit(uint256 _pricePercentageLimit) external onlyLRTAdmin {
    pricePercentageLimit = _pricePercentageLimit;          // no minimum check
    emit PricePercentageLimitUpdate(_pricePercentageLimit);
}
```

Additionally, `pricePercentageLimit` is never assigned in `initialize`, so it defaults to `0` on every fresh deployment — meaning both protections are off-by-default until the admin explicitly sets a non-zero value.

---

### Impact Explanation

**Impact: High — Theft of unclaimed yield.**

When `pricePercentageLimit == 0` and the rsETH price drops materially (e.g., due to an EigenLayer slashing event reducing total ETH in the protocol):

1. `_updateRsETHPrice` is called (publicly callable via `updateRSETHPrice()`).
2. `isPriceDecreaseOffLimit` is `false`; the auto-pause branch is skipped.
3. The new, lower `rsETHPrice` is committed to storage.
4. Deposits remain open. New depositors calling `depositETH` / `depositAsset` receive rsETH minted at the depressed price — i.e., more rsETH per unit of ETH than the protocol's backing warrants.
5. This inflates rsETH supply relative to backing, permanently diluting every existing rsETH holder's proportional claim on protocol assets — a direct theft of accrued yield from current holders.

The upside gate being disabled is a secondary concern: without it, any unprivileged address can call `updateRSETHPrice()` and commit an anomalously high price (if the underlying oracle is ever manipulated), bypassing the manager-only override that normally gates such updates.

---

### Likelihood Explanation

The admin can reach this state in two ways:

1. **Default state**: `pricePercentageLimit` is `0` at deployment and remains `0` until explicitly configured. Any window before the admin calls `setPricePercentageLimit` with a non-zero value leaves both protections inactive.
2. **Accidental reset**: An admin intending to temporarily relax the limit (e.g., during a migration or large reward event) calls `setPricePercentageLimit(0)`, not realising this disables the mechanism entirely rather than simply widening the band.

Both paths are realistic operational mistakes, matching the "accidental misconfiguration" pattern of the reference report.

---

### Recommendation

Introduce a non-zero minimum constant and enforce it in the setter:

```solidity
uint256 public constant MIN_PRICE_PERCENTAGE_LIMIT = 1e15; // 0.1% minimum

function setPricePercentageLimit(uint256 _pricePercentageLimit) external onlyLRTAdmin {
    if (_pricePercentageLimit < MIN_PRICE_PERCENTAGE_LIMIT) revert PricePercentageLimitTooLow();
    pricePercentageLimit = _pricePercentageLimit;
    emit PricePercentageLimitUpdate(_pricePercentageLimit);
}
```

Additionally, assign a safe default in `initialize` so the protection is active from the first block:

```solidity
pricePercentageLimit = 5e16; // 5% default
```

---

### Proof of Concept

1. Admin calls `setPricePercentageLimit(0)` (or the contract is freshly deployed and the setter was never called).
2. A slashing event reduces total ETH in the protocol by 10 %, causing `newRsETHPrice` to fall 10 % below `highestRsethPrice`.
3. Any address calls `updateRSETHPrice()`.
4. Inside `_updateRsETHPrice`:
   - `diff = highestRsethPrice - newRsETHPrice` (non-zero).
   - `isPriceDecreaseOffLimit = 0 > 0 && …` → `false`.
   - Auto-pause branch is skipped; `rsETHPrice` is updated to the lower value.
5. `LRTDepositPool` and `LRTWithdrawalManager` remain unpaused.
6. A new depositor calls `depositETH{value: 1 ether}(…)`.
7. `getRsETHAmountToMint` uses the now-lower `rsETHPrice`, minting more rsETH per ETH than the protocol's backing supports.
8. All pre-existing rsETH holders' proportional backing is permanently diluted — yield is effectively transferred from existing holders to the new depositor.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** contracts/LRTOracle.sol (L125-128)
```text
    function setPricePercentageLimit(uint256 _pricePercentageLimit) external onlyLRTAdmin {
        pricePercentageLimit = _pricePercentageLimit;
        emit PricePercentageLimitUpdate(_pricePercentageLimit);
    }
```

**File:** contracts/LRTOracle.sol (L256-266)
```text
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

**File:** contracts/LRTOracle.sol (L273-282)
```text
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
