### Title
Stale `rsETHPrice` After Auto-Pause Creates Permanent Withdrawal Queue Freeze — (File: `contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle._updateRsETHPrice()` auto-pauses the oracle and returns **without updating `rsETHPrice`** when a price drop exceeds `pricePercentageLimit`. After this self-invalidation, neither the public `updateRSETHPrice()` (blocked by `whenNotPaused`) nor `updateRSETHPriceAsManager()` (which calls the same `_updateRsETHPrice()` and re-triggers the pause) can advance the stored price. The manager has an explicit bypass for the **upside** threshold but no equivalent bypass for the **downside** case. If the actual price never recovers above the threshold, `rsETHPrice` is permanently stale and the withdrawal queue is permanently frozen.

---

### Finding Description

`LRTOracle._updateRsETHPrice()` contains asymmetric threshold handling:

**Upside case — manager bypass exists:** [1](#0-0) 

The manager can call `updateRSETHPriceAsManager()` and the check `hasRole(MANAGER, msg.sender)` allows the price to be written even when it exceeds the upside limit.

**Downside case — no manager bypass:** [2](#0-1) 

When `isPriceDecreaseOffLimit` is true, `_updateRsETHPrice()` unconditionally pauses the oracle and **returns before writing `rsETHPrice`**. There is no role check that lets the manager skip this branch.

Both public entry points call the same internal function: [3](#0-2) 

After auto-pause:
- `updateRSETHPrice()` reverts at `whenNotPaused`.
- `updateRSETHPriceAsManager()` reaches `_updateRsETHPrice()`, re-detects `isPriceDecreaseOffLimit`, re-pauses, and returns — `rsETHPrice` is never written.

`highestRsethPrice` is only updated upward: [4](#0-3) 

So the recovery bar is always the all-time peak, making partial recoveries insufficient to exit the frozen state.

There is no setter for `rsETHPrice` or `highestRsethPrice` anywhere in `LRTOracle.sol`, so no admin escape hatch exists.

The withdrawal manager's `unlockQueue()` and `completeWithdrawal()` are gated by `whenNotPaused`: [5](#0-4) 

And `unlockQueue()` reads the stale price directly: [6](#0-5) 

---

### Impact Explanation

Users who called `initiateWithdrawal()` have their rsETH locked inside `LRTWithdrawalManager`. If the oracle auto-pauses and the price does not recover above `highestRsethPrice * (1 - pricePercentageLimit)`, the withdrawal queue can never be processed: `unlockQueue()` is blocked by the withdrawal manager's pause, and even if the admin unpauses the withdrawal manager independently, `unlockQueue()` would use the stale (inflated) `rsETHPrice`, causing the protocol to over-pay users and risk insolvency. The locked rsETH constitutes **temporary (potentially permanent) freezing of user funds**.

**Impact: Medium — Temporary freezing of funds; escalates to Critical (permanent freeze / insolvency) if price does not recover.**

---

### Likelihood Explanation

EigenLayer restaking carries real slashing risk. A correlated slashing event across multiple validators could cause the rsETH NAV to drop sharply. Any caller (unprivileged) can trigger the auto-pause by calling `updateRSETHPrice()` once the on-chain price has already fallen. The condition is realistic for a liquid restaking protocol and requires no privileged access to trigger.

---

### Recommendation

Mirror the upside bypass for the downside case: allow the LRT manager to call a dedicated function (e.g., `updateRSETHPriceAsManager()`) that skips the downside auto-pause and writes the new price directly, accepting the risk consciously. Alternatively, add a separate `setRsETHPrice(uint256)` admin function that can be used to manually advance the stored price after a governance decision, analogous to the fix suggested in the reference report (allow price updates without the invalidated mechanism once the normal path is broken).

---

### Proof of Concept

1. Protocol operates normally; `highestRsethPrice = 1.05 ether`, `pricePercentageLimit = 5e16` (5%).
2. A slashing event reduces the actual NAV so `newRsETHPrice = 0.99 ether` (5.7% below peak).
3. Any address calls `updateRSETHPrice()`.
4. `_updateRsETHPrice()` computes `isPriceDecreaseOffLimit = true`, calls `_pause()`, and **returns** — `rsETHPrice` stays at `1.05 ether`.
5. Admin unpauses the oracle. Manager calls `updateRSETHPriceAsManager()`. Same branch fires, oracle re-pauses, `rsETHPrice` unchanged.
6. Users with rsETH locked in `LRTWithdrawalManager` (from prior `initiateWithdrawal()` calls) cannot call `completeWithdrawal()` or have their requests processed via `unlockQueue()` — both revert due to the withdrawal manager's pause.
7. If the price never recovers to `≥ 0.9975 ether` (i.e., within 5% of `highestRsethPrice`), the withdrawal queue is permanently frozen with no on-chain mechanism to resolve it.

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

**File:** contracts/LRTOracle.sol (L260-265)
```text
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
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

**File:** contracts/LRTOracle.sol (L293-296)
```text
        // update highest price if new price exceeds it
        if (newRsETHPrice > highestRsethPrice) {
            highestRsethPrice = newRsETHPrice;
        }
```

**File:** contracts/LRTWithdrawalManager.sol (L268-281)
```text
    function unlockQueue(
        address asset,
        uint256 firstExcludedIndex,
        uint256 minimumAssetPrice,
        uint256 minimumRsEthPrice,
        uint256 maximumAssetPrice,
        uint256 maximumRsEthPrice
    )
        external
        nonReentrant
        onlySupportedAsset(asset)
        whenNotPaused
        onlyAssetTransferOrOperatorRole
        returns (uint256 rsETHBurned, uint256 assetAmountUnlocked)
```

**File:** contracts/LRTWithdrawalManager.sol (L847-850)
```text
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
```
