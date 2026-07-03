### Title
No Upper Bound on `pricePercentageLimit` Allows Disabling Price Deviation Protection - (File: contracts/LRTOracle.sol)

### Summary
`LRTOracle.setPricePercentageLimit()` accepts any `uint256` value with no maximum cap enforced. The contract's own comments document that `1e18` equals 100%. Setting the limit to `1e18` (or higher) silently disables both the upside revert guard and the downside auto-pause mechanism that protect the protocol from abnormal rsETH price movements.

### Finding Description
`setPricePercentageLimit` stores the caller-supplied value directly with no upper-bound validation:

```solidity
// contracts/LRTOracle.sol L121-128
/// @dev PricePercentageLimit for 1% is 1e16
/// @dev Price Percentage Limit for 100% is 1e18
function setPricePercentageLimit(uint256 _pricePercentageLimit) external onlyLRTAdmin {
    pricePercentageLimit = _pricePercentageLimit;
    emit PricePercentageLimitUpdate(_pricePercentageLimit);
}
``` [1](#0-0) 

This value is consumed in two places inside `_updateRsETHPrice()`:

**Upside guard** — reverts for non-managers if the new price exceeds `highestRsethPrice` by more than the limit: [2](#0-1) 

**Downside guard** — auto-pauses the deposit pool, withdrawal manager, and oracle if the new price falls more than the limit below `highestRsethPrice`: [3](#0-2) 

When `pricePercentageLimit = 1e18` (100%):

- **Upside**: a price increase of up to 100% from the all-time high passes the check, so any realistic oracle spike goes undetected by non-managers.
- **Downside**: `diff > pricePercentageLimit.mulWad(highestRsethPrice)` requires `diff > highestRsethPrice`, i.e. the price must go negative — mathematically impossible. The auto-pause **never fires**.

Values above `1e18` (e.g. `2e18`) produce the same result and are equally accepted.

### Impact Explanation
The downside auto-pause is the last-resort safety net against slashing events, accounting bugs, or oracle manipulation that cause the rsETH price to collapse. With the limit set to 100%, the protocol will never auto-pause on a price drop, allowing deposits and withdrawals to continue at a manipulated or erroneous price. This constitutes the contract failing to deliver its promised safety guarantee.

**Impact: Low — Contract fails to deliver promised returns, but doesn't lose value directly.**

### Likelihood Explanation
The setter is `onlyLRTAdmin`. A misconfiguration (e.g. intending to "allow any change temporarily" or a unit confusion between basis-points and WAD precision) could produce a value of `1e18`. The contract provides no feedback that 100% disables the guard entirely. Likelihood is low but non-negligible given the precision ambiguity documented in the comments themselves.

### Recommendation
Add an explicit upper-bound cap in `setPricePercentageLimit`. A reasonable maximum is 10% (`1e17`), analogous to the external report's recommendation of `1000` basis points:

```solidity
uint256 public constant MAX_PRICE_PERCENTAGE_LIMIT = 1e17; // 10%

function setPricePercentageLimit(uint256 _pricePercentageLimit) external onlyLRTAdmin {
    if (_pricePercentageLimit > MAX_PRICE_PERCENTAGE_LIMIT) revert PricePercentageLimitTooHigh();
    pricePercentageLimit = _pricePercentageLimit;
    emit PricePercentageLimitUpdate(_pricePercentageLimit);
}
```

### Proof of Concept
1. Admin calls `setPricePercentageLimit(1e18)` — accepted with no revert.
2. An oracle or accounting error causes `newRsETHPrice` to drop to `1 wei` while `highestRsethPrice = 1 ether`.
3. `diff = 1 ether - 1 wei ≈ 1 ether`; `pricePercentageLimit.mulWad(highestRsethPrice) = 1e18 * 1 ether / 1e18 = 1 ether`.
4. Condition `diff > 1 ether` is `false` → `isPriceDecreaseOffLimit = false` → **no pause triggered**.
5. `rsETHPrice` is updated to `1 wei`; subsequent depositors and withdrawers interact at a near-zero rsETH price with no protocol-level protection. [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/LRTOracle.sol (L121-128)
```text
    /// @dev set the price percentage limit. Only onlyLRTAdmin is allowed
    /// @dev PricePercentageLimit for 1% is 1e16
    /// @dev Price Percentage Limit for 100% is 1e18
    /// @param _pricePercentageLimit price percentage limit
    function setPricePercentageLimit(uint256 _pricePercentageLimit) external onlyLRTAdmin {
        pricePercentageLimit = _pricePercentageLimit;
        emit PricePercentageLimitUpdate(_pricePercentageLimit);
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
