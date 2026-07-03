### Title
Unbounded `setPricePercentageLimit` Setter Allows Misconfiguration to Disable Price Circuit Breaker or Trigger Protocol-Wide Pause — (File: contracts/LRTOracle.sol)

---

### Summary

`LRTOracle.setPricePercentageLimit` accepts any `uint256` value with no bounds validation, despite the NatSpec comments explicitly documenting the required scale (`1e16` = 1%, `1e18` = 100%). A value set off by even a few decimal places either permanently disables the price circuit breaker (value too large) or causes the protocol to auto-pause on any sub-wei price movement (value too small), in both cases reachable by any public caller of `updateRSETHPrice()`.

---

### Finding Description

`LRTOracle.sol` stores a `pricePercentageLimit` variable used as a WAD-scaled (1e18 = 100%) threshold for both upside and downside price protection:

```solidity
/// @dev PricePercentageLimit for 1% is 1e16
/// @dev Price Percentage Limit for 100% is 1e18
function setPricePercentageLimit(uint256 _pricePercentageLimit) external onlyLRTAdmin {
    pricePercentageLimit = _pricePercentageLimit;   // ← no validation
    emit PricePercentageLimitUpdate(_pricePercentageLimit);
}
``` [1](#0-0) 

The value is consumed in `_updateRsETHPrice()` via `mulWad` (i.e., `a * b / 1e18`) for both the upside check and the downside auto-pause:

```solidity
bool isPriceIncreaseOffLimit =
    pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
``` [2](#0-1) 

```solidity
bool isPriceDecreaseOffLimit =
    pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

if (isPriceDecreaseOffLimit) {
    if (!lrtDepositPool.paused()) lrtDepositPool.pause();
    if (!withdrawalManager.paused()) withdrawalManager.pause();
    _pause();
    return;
}
``` [3](#0-2) 

`updateRSETHPrice()` is a public, permissionless function:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [4](#0-3) 

**Misconfiguration path A — value too small (e.g., `1` instead of `1e16` for 1%):**

`pricePercentageLimit.mulWad(highestRsethPrice)` = `1 * 1e18 / 1e18 = 1`. Any price decrease of more than 1 wei satisfies `diff > 1`, triggering `isPriceDecreaseOffLimit = true`. The next public call to `updateRSETHPrice()` pauses `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle` simultaneously.

**Misconfiguration path B — value too large (e.g., `1e35` instead of `1e16`):**

`pricePercentageLimit.mulWad(highestRsethPrice)` = `1e35`. No realistic price movement can exceed this threshold, so `isPriceIncreaseOffLimit` and `isPriceDecreaseOffLimit` are permanently `false`. Both the upside revert guard and the downside auto-pause are silently disabled.

---

### Impact Explanation

**Path A (value too small):** Any unprivileged caller invoking `updateRSETHPrice()` after even a 1-wei price decrease will atomically pause `LRTDepositPool` and `LRTWithdrawalManager`, freezing all deposits and withdrawals for all users. Recovery requires an admin to unpause each contract individually. This constitutes **temporary freezing of funds (Medium)**.

**Path B (value too large):** The downside auto-pause is silently disabled. If the rsETH price drops significantly (e.g., due to slashing or oracle drift), the protocol will not self-protect, and users can continue withdrawing at a stale or manipulated price, leading to **protocol insolvency (Critical)**.

---

### Likelihood Explanation

The NatSpec comment documents the scale as `1e16` for 1% and `1e18` for 100%, but the setter accepts any `uint256`. An admin intending to set a 1% limit who passes `1e15` (off by one decimal place) or `1` (off by 16 decimal places) silently misconfigures the circuit breaker. This is the exact class of mistake documented in the reference report. The misconfiguration is not detectable on-chain until `updateRSETHPrice()` is called.

---

### Recommendation

Add explicit bounds validation to `setPricePercentageLimit`:

```solidity
function setPricePercentageLimit(uint256 _pricePercentageLimit) external onlyLRTAdmin {
    // Must be between 0.01% (1e14) and 100% (1e18), or zero to disable
    require(
        _pricePercentageLimit == 0 ||
        (_pricePercentageLimit >= 1e14 && _pricePercentageLimit <= 1e18),
        "LRTOracle: price percentage limit out of range"
    );
    pricePercentageLimit = _pricePercentageLimit;
    emit PricePercentageLimitUpdate(_pricePercentageLimit);
}
```

Similarly, add bounds validation to `setMaxFeeMintAmountPerDay` to prevent a zero value from bricking `updateRSETHPrice()` when fees are non-zero. [5](#0-4) 

---

### Proof of Concept

1. Admin calls `setPricePercentageLimit(1)` intending `1e16` (1%), off by 16 decimal places.
2. rsETH price decreases by 2 wei due to normal stETH rebasing.
3. Attacker (any EOA) calls `updateRSETHPrice()`.
4. Inside `_updateRsETHPrice()`: `diff = 2`, `pricePercentageLimit.mulWad(highestRsethPrice) = 1`, so `isPriceDecreaseOffLimit = true`.
5. `LRTDepositPool.pause()`, `LRTWithdrawalManager.pause()`, and `LRTOracle._pause()` are all called.
6. All user deposits and withdrawals are frozen. No user can interact with the protocol until an admin manually unpauses each contract. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

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

**File:** contracts/LRTOracle.sol (L132-135)
```text
    function setMaxFeeMintAmountPerDay(uint256 _maxFeeMintAmountPerDay) external onlyLRTManager {
        maxFeeMintAmountPerDay = _maxFeeMintAmountPerDay;
        emit MaxFeeMintAmountPerDayUpdated(_maxFeeMintAmountPerDay);
    }
```

**File:** contracts/LRTOracle.sol (L256-257)
```text
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
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
