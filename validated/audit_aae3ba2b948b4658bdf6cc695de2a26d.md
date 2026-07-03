Audit Report

## Title
`LRTOracle._updateRsETHPrice()` Has No Manager Bypass for Large Price Decreases, Temporarily Freezing Funds After Significant Slashing - (File: `contracts/LRTOracle.sol`)

## Summary
`_updateRsETHPrice()` provides a manager-role bypass for large price *increases* but unconditionally pauses the protocol and returns early on large price *decreases*, with no equivalent bypass. Because `updateRSETHPriceAsManager()` lacks `whenNotPaused` but still calls the same internal function, the manager cannot update the price while `isPriceDecreaseOffLimit` remains true. Deposits and withdrawals are frozen until an admin manually reconfigures `pricePercentageLimit`.

## Finding Description
The asymmetry is confirmed in `_updateRsETHPrice()`:

**Price increase path** (lines 260–266): if `isPriceIncreaseOffLimit` is true and the caller holds `MANAGER` role, execution falls through and `rsETHPrice` is updated at line 313.

**Price decrease path** (lines 277–282): if `isPriceDecreaseOffLimit` is true, the function unconditionally calls `lrtDepositPool.pause()`, `withdrawalManager.pause()`, `_pause()`, and `return` — with no role check and no bypass. `rsETHPrice = newRsETHPrice` at line 313 is never reached.

`updateRSETHPriceAsManager()` (line 94) has no `whenNotPaused` modifier, so it can be called while paused. However, it still delegates to `_updateRsETHPrice()`, which re-evaluates the same `isPriceDecreaseOffLimit` condition. Since the underlying TVL has not changed, the condition remains true, the function pauses again (idempotently via `_pause()` at lines 319–323) and returns early again. The price is permanently stuck at the pre-slashing `highestRsethPrice` until an admin calls `setPricePercentageLimit(0)` (lines 125–128), which is undocumented as a recovery mechanism.

## Impact Explanation
**Medium. Temporary freezing of funds.**

When a slashing event reduces TVL such that `newRsETHPrice` drops more than `pricePercentageLimit` below `highestRsethPrice`: `LRTDepositPool` is paused (no deposits), `LRTWithdrawalManager` is paused (no withdrawals, user funds frozen), and `LRTOracle` is paused. The protocol is stuck in a pause loop until an admin takes an out-of-band remediation step. The freeze is temporary (not permanent) because the admin retains the ability to call `setPricePercentageLimit(0)`, but this path is non-obvious and undocumented, making the freeze potentially extended in practice. The impact does not reach Critical/permanent because admin recourse exists.

## Likelihood Explanation
The protocol integrates with EigenLayer AVS strategies and EigenPods, explicitly exposing it to slashing risk. A single large slashing event or accumulated smaller events can push the TVL drop beyond `pricePercentageLimit`. If `pricePercentageLimit` is set conservatively (e.g., 1% = `1e16`), the threshold is easy to breach. The trigger requires no privileged access — any public caller invoking `updateRSETHPrice()` after a slashing event will trigger the pause. Likelihood is Medium.

## Recommendation
Mirror the price-increase manager bypass for the price-decrease path. When `isPriceDecreaseOffLimit` is true and the caller holds `MANAGER` role, allow the price update to proceed (with an emitted warning event) rather than unconditionally pausing and returning:

```solidity
if (isPriceDecreaseOffLimit) {
    if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
        if (!lrtDepositPool.paused()) lrtDepositPool.pause();
        if (!withdrawalManager.paused()) withdrawalManager.pause();
        _pause();
        return;
    }
    // manager: emit warning, allow price update to proceed
    emit RsETHPriceLargeDecrease(newRsETHPrice, highestRsethPrice);
}
```

This makes the decrease path symmetric with the increase path and eliminates the need for out-of-band admin reconfiguration after a slashing event.

## Proof of Concept

1. Deploy with `highestRsethPrice = 1.05e18`, `pricePercentageLimit = 1e16` (1%).
2. A slashing event reduces TVL so that `_getTotalEthInProtocol()` yields `newRsETHPrice = 1.03e18` (~1.9% drop, exceeding the 1% limit).
3. Any account calls `updateRSETHPrice()` (public, `whenNotPaused`).
4. Inside `_updateRsETHPrice()`: `diff = 0.02e18 > pricePercentageLimit.mulWad(1.05e18) = 1.05e16` → `isPriceDecreaseOffLimit = true` → `lrtDepositPool.pause()`, `withdrawalManager.pause()`, `_pause()`, `return`. `rsETHPrice` remains `1.05e18`.
5. Admin unpauses all three contracts.
6. Manager calls `updateRSETHPriceAsManager()` — TVL unchanged, same condition fires, protocol re-pauses, price still not updated.
7. Repeat indefinitely. User withdrawals remain frozen. `rsETHPrice` is stuck at `1.05e18` despite actual value being `1.03e18`.
8. Only escape: admin calls `setPricePercentageLimit(0)`, then manager calls `updateRSETHPriceAsManager()` successfully. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/LRTOracle.sol (L87-96)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }

    /// @dev update rsETH price as an manager account
    /// @dev main benefit is to be able to update the price in case of the price going above the threshold
    /// @dev only LRT manager is allowed to call this function
    function updateRSETHPriceAsManager() external onlyLRTManager {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L125-128)
```text
    function setPricePercentageLimit(uint256 _pricePercentageLimit) external onlyLRTAdmin {
        pricePercentageLimit = _pricePercentageLimit;
        emit PricePercentageLimitUpdate(_pricePercentageLimit);
    }
```

**File:** contracts/LRTOracle.sol (L260-266)
```text
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
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

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/LRTOracle.sol (L319-323)
```text
    function _pause() internal {
        if (paused) return;
        paused = true;
        emit Paused(msg.sender);
    }
```
