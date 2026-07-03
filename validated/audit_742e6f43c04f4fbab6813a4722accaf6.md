### Title
`LRTOracle._updateRsETHPrice()` Has No Manager Bypass for Large Price Decreases, Permanently Freezing Funds After Significant Slashing - (File: `contracts/LRTOracle.sol`)

### Summary
`LRTOracle._updateRsETHPrice()` supports large price *increases* via a manager bypass, but provides no equivalent bypass for large price *decreases*. When slashing reduces the protocol TVL enough to push `newRsETHPrice` more than `pricePercentageLimit` below `highestRsethPrice`, the function unconditionally pauses the protocol and returns early **without updating `rsETHPrice`**. No code path — including the manager-only `updateRSETHPriceAsManager()` — can update the price while this condition persists, leaving the protocol permanently frozen until an admin manually reconfigures `pricePercentageLimit`.

### Finding Description
`_updateRsETHPrice()` contains two asymmetric threshold checks:

**For price increases** — the manager can bypass the daily threshold and update the price:
```solidity
if (isPriceIncreaseOffLimit) {
    if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
        revert PriceAboveDailyThreshold();
    }
    // manager falls through → price IS updated
}
```

**For price decreases** — there is no bypass; the function always pauses and returns early:
```solidity
if (isPriceDecreaseOffLimit) {
    if (!lrtDepositPool.paused()) lrtDepositPool.pause();
    if (!withdrawalManager.paused()) withdrawalManager.pause();
    _pause();
    return;   // rsETHPrice is NEVER updated
}
```

Because `_pause()` is idempotent (`if (paused) return;`), calling `updateRSETHPriceAsManager()` after the protocol is already paused still hits the same `return` before `rsETHPrice = newRsETHPrice` is reached. The price is permanently stuck at the pre-slashing `highestRsethPrice` value.

The only escape is for an admin to call `setPricePercentageLimit(0)`, which is undocumented and non-obvious, or to upgrade the contract.

### Impact Explanation
**Impact: Medium — Temporary (potentially extended) freezing of funds.**

When a slashing event reduces TVL by more than `pricePercentageLimit` relative to `highestRsethPrice`:
- `LRTDepositPool` is paused → no new deposits.
- `LRTWithdrawalManager` is paused → no withdrawals; user funds are frozen.
- `LRTOracle` is paused → `updateRSETHPrice()` (which has `whenNotPaused`) cannot be called by the public.
- `updateRSETHPriceAsManager()` (no `whenNotPaused`) still calls `_updateRsETHPrice()`, which re-evaluates the same condition and returns early again without updating the price.

The protocol is stuck in a loop: unpause → call update → re-pause → repeat. User funds remain frozen until an admin takes an out-of-band remediation step.

### Likelihood Explanation
**Likelihood: Medium.**

EigenLayer restaking exposes the protocol to AVS slashing. A single large slashing event, or accumulated smaller events, can push the TVL drop beyond `pricePercentageLimit`. The protocol explicitly acknowledges slashing risk in its architecture (EigenPod and strategy integrations). `pricePercentageLimit` is configurable and may be set conservatively (e.g., 1%), making the threshold easy to breach.

### Recommendation
Mirror the price-increase manager bypass for price decreases. Allow a caller with `MANAGER` role to update `rsETHPrice` even when `isPriceDecreaseOffLimit` is true, so the protocol can reflect actual slashing losses and resume normal operation without requiring an admin to reconfigure `pricePercentageLimit`:

```solidity
if (isPriceDecreaseOffLimit) {
    if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
        // non-manager: pause and return
        if (!lrtDepositPool.paused()) lrtDepositPool.pause();
        if (!withdrawalManager.paused()) withdrawalManager.pause();
        _pause();
        return;
    }
    // manager: emit warning but allow price update to proceed
    emit RsETHPriceLargeDecrease(newRsETHPrice, highestRsethPrice);
}
```

### Proof of Concept

1. Protocol is live; `highestRsethPrice = 1.05e18`, `pricePercentageLimit = 1e16` (1%).
2. A significant slashing event reduces TVL; `_getTotalEthInProtocol()` now yields a value such that `newRsETHPrice = 1.03e18` — a ~1.9% drop from `highestRsethPrice`, exceeding the 1% limit.
3. Anyone calls `updateRSETHPrice()` (public, `whenNotPaused`).
4. Inside `_updateRsETHPrice()`:
   - `diff = 1.05e18 - 1.03e18 = 0.02e18`
   - `pricePercentageLimit.mulWad(highestRsethPrice) = 1e16 * 1.05e18 / 1e18 = 1.05e16`
   - `isPriceDecreaseOffLimit = 0.02e18 > 1.05e16` → **true**
   - `lrtDepositPool.pause()`, `withdrawalManager.pause()`, `_pause()` are called.
   - Function **returns** — `rsETHPrice` remains `1.05e18`.
5. Admin calls `unpause()` on all three contracts.
6. Manager calls `updateRSETHPriceAsManager()` — TVL unchanged, same calculation, same `isPriceDecreaseOffLimit = true` → protocol pauses again, price still not updated.
7. User funds remain frozen. `rsETHPrice` is permanently stuck at `1.05e18` despite actual protocol value being `1.03e18`.

**Relevant code:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/LRTOracle.sol (L87-95)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }

    /// @dev update rsETH price as an manager account
    /// @dev main benefit is to be able to update the price in case of the price going above the threshold
    /// @dev only LRT manager is allowed to call this function
    function updateRSETHPriceAsManager() external onlyLRTManager {
        _updateRsETHPrice();
```

**File:** contracts/LRTOracle.sol (L244-282)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

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
```

**File:** contracts/LRTOracle.sol (L319-323)
```text
    function _pause() internal {
        if (paused) return;
        paused = true;
        emit Paused(msg.sender);
    }
```
