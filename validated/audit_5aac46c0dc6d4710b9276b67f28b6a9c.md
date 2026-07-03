### Title
Protocol fees on yield accrued during pause are permanently lost when `updateRSETHPriceAsManager()` is called while paused - (File: contracts/LRTOracle.sol)

---

### Summary

`LRTOracle._updateRsETHPrice()` skips protocol fee collection when the protocol is paused, but still unconditionally writes the new `rsETHPrice` to storage. When the manager calls `updateRSETHPriceAsManager()` during a pause, the price advances to reflect yield that accrued during the pause period — but no fee is minted to the treasury. After unpausing, `previousTVL` is already anchored to the post-yield price, so the next `updateRSETHPrice()` call sees no incremental TVL gain and collects no fee. The protocol treasury permanently loses its fee share on all yield that accrued during the pause.

---

### Finding Description

`_updateRsETHPrice()` computes `previousTVL` from the stored `rsETHPrice`:

```solidity
uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);
```

It then gates fee collection on `protocolPaused`:

```solidity
bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;

if (!protocolPaused && totalETHInProtocol > previousTVL) {
    uint256 rewardAmount = totalETHInProtocol - previousTVL;
    protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
}
```

Regardless of whether a fee was collected, the function always commits the new price to storage at the end:

```solidity
rsETHPrice = newRsETHPrice;
```

`updateRSETHPriceAsManager()` has no `whenNotPaused` guard:

```solidity
function updateRSETHPriceAsManager() external onlyLRTManager {
    _updateRsETHPrice();
}
```

So when the manager calls this function during a pause:
- `protocolPaused = true` → `protocolFeeInETH = 0` (no fee minted)
- `rsETHPrice` is updated to the current value, which already embeds the yield that accrued during the pause

On the next `updateRSETHPrice()` call after unpausing:
- `previousTVL = rsethSupply * newRsETHPrice` (the post-yield price set during the pause)
- `totalETHInProtocol ≈ previousTVL` (no new yield since the manager's update)
- No fee is collected

The yield that accrued during the pause is permanently absorbed into the rsETH price without the treasury receiving its fee share.

This is the direct analog of the Balancer finding: in Balancer, the recovery-mode flag is cleared **before** pool data is loaded, causing yield fees to be deducted from balances but never attributed to the fee collector. Here, `rsETHPrice` is advanced **while fees are suppressed**, causing the yield window to be consumed without fee attribution.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

The protocol treasury permanently loses its `protocolFeeInBPS` share of all yield that accrued during the pause period. The yield is not destroyed; it is silently redistributed to rsETH holders (their tokens appreciate by the full yield amount rather than yield minus fee). The magnitude scales with the duration of the pause and the yield rate of the underlying LSTs (e.g., stETH). For a multi-day pause on a large TVL, this can represent a material loss of treasury revenue.

---

### Likelihood Explanation

The auto-pause mechanism in `_updateRsETHPrice()` triggers automatically when the price drops beyond `pricePercentageLimit`:

```solidity
if (isPriceDecreaseOffLimit) {
    if (!lrtDepositPool.paused()) lrtDepositPool.pause();
    if (!withdrawalManager.paused()) withdrawalManager.pause();
    _pause();
    return;
}
```

After an auto-pause, the manager is expected to investigate and call `updateRSETHPriceAsManager()` — the function's own NatSpec states its "main benefit is to be able to update the price in case of the price going above the threshold." This is the intended operational flow. The fee loss occurs as a silent side effect of that normal operation. No malicious intent is required; the manager is doing exactly what the protocol design expects.

---

### Recommendation

**Short term:** In `_updateRsETHPrice()`, do not write `rsETHPrice` to storage when `protocolPaused == true`. This preserves the pre-pause `previousTVL` anchor so that the full yield window (including the pause period) is subject to fee collection on the first post-unpause price update.

**Long term:** Add an explicit test that verifies protocol fees are correctly collected on yield that accrues across a pause/unpause cycle, including the case where `updateRSETHPriceAsManager()` is called during the pause.

---

### Proof of Concept

1. Protocol is running. `rsETHPrice = 1.05e18`, `rsethSupply = 1000e18`. `previousTVL = 1050e18`.
2. Price drops beyond `pricePercentageLimit`. Auto-pause fires: `lrtDepositPool`, `withdrawalManager`, and `LRTOracle` are all paused. `rsETHPrice` is **not** updated (the function returns early).
3. Over the next 7 days, stETH yield accrues. `totalETHInProtocol` grows from `1050e18` to `1057e18` (≈0.67% yield).
4. Manager calls `updateRSETHPriceAsManager()`:
   - `protocolPaused = true` → `protocolFeeInETH = 0`
   - `newRsETHPrice = 1057e18 / 1000 = 1.057e18`
   - `rsETHPrice = 1.057e18` ✅ written to storage
5. Admin unpauses all three contracts.
6. Anyone calls `updateRSETHPrice()`:
   - `previousTVL = 1000e18 * 1.057e18 = 1057e18`
   - `totalETHInProtocol ≈ 1057e18`
   - `totalETHInProtocol ≤ previousTVL` → `protocolFeeInETH = 0`
7. Treasury receives **zero** fee on the `7e18` ETH of yield. At a 10% protocol fee, `0.7e18` ETH worth of rsETH is permanently lost to the treasury. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** contracts/LRTOracle.sol (L234-247)
```text
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);

        IPausable lrtDepositPool = IPausable(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        IPausable withdrawalManager = IPausable(lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER));

        // determine if the protocol is active (not paused)
        bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;

        // only take fee if TVL increased and protocol is not paused
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
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
