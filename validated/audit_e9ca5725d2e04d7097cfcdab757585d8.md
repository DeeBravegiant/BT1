### Title
`highestRsethPrice` Not Reset on `unpause()` Allows Any Caller to Permanently Re-Trigger Protocol Auto-Pause — (File: `contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle.unpause()` resets only the `paused` flag but never resets `highestRsethPrice`. Because `_updateRsETHPrice()` auto-pauses the entire protocol whenever the current price is below `highestRsethPrice` by more than `pricePercentageLimit`, any unprivileged caller can immediately re-pause the protocol after an admin unpause by calling the public `updateRSETHPrice()`, as long as the price has not recovered. This makes the admin's unpause action permanently ineffective and freezes user funds.

---

### Finding Description

`_updateRsETHPrice()` contains a downside-protection mechanism: [1](#0-0) 

When the price drop exceeds the limit, the function pauses the oracle itself, the deposit pool, and the withdrawal manager, then returns early without updating `highestRsethPrice`. [2](#0-1) 

When the admin later calls `unpause()`, only `paused` is set to `false`. `highestRsethPrice` is never touched: [3](#0-2) 

`highestRsethPrice` is only ever updated upward, inside `_updateRsETHPrice()`, when the new price exceeds the previous peak: [4](#0-3) 

Because `highestRsethPrice` is never reset downward, the condition `diff > pricePercentageLimit.mulWad(highestRsethPrice)` remains `true` after the unpause if the price has not recovered. The public entry point `updateRSETHPrice()` is gated only by `whenNotPaused`: [5](#0-4) 

So immediately after the admin unpauses the oracle, any external caller can invoke `updateRSETHPrice()`, which re-executes `_updateRsETHPrice()`, re-triggers the auto-pause on the oracle, the deposit pool, and the withdrawal manager, and the cycle repeats indefinitely.

---

### Impact Explanation

Every time the admin unpauses the protocol, any unprivileged caller can re-pause it in the same block by calling `updateRSETHPrice()`. Users cannot deposit into `LRTDepositPool` or complete withdrawals through `LRTWithdrawalManager` while paused. Because there is no setter for `highestRsethPrice` and no way to reset it without a contract upgrade or setting `pricePercentageLimit = 0`, the protocol is effectively frozen until the rsETH price recovers above the historical peak minus the threshold — an external condition the protocol cannot control. This constitutes **temporary (potentially extended) freezing of funds**.

---

### Likelihood Explanation

The initial trigger requires a genuine price drop exceeding `pricePercentageLimit` relative to `highestRsethPrice`, which is a realistic market event for a liquid restaking token. Once triggered, the griefing requires only gas — no capital, no role, no special access. Any address can call `updateRSETHPrice()` after each admin unpause. The cost to the attacker is negligible; the cost to the protocol is indefinite fund freeze.

---

### Recommendation

Reset `highestRsethPrice` to the current `rsETHPrice` inside `unpause()` so that the auto-pause threshold is recalibrated to the post-recovery baseline:

```solidity
function unpause() external whenPaused onlyLRTAdmin {
    paused = false;
    highestRsethPrice = rsETHPrice; // recalibrate peak to current price
    emit Unpaused(msg.sender);
}
```

Alternatively, add a dedicated admin function to reset `highestRsethPrice` independently of the pause state.

---

### Proof of Concept

1. `highestRsethPrice` is set to `P_high` during normal operation.
2. rsETH price drops to `P_low` where `P_high − P_low > pricePercentageLimit × P_high`.
3. Anyone calls `updateRSETHPrice()` → `_updateRsETHPrice()` auto-pauses oracle, deposit pool, and withdrawal manager.
4. Admin calls `unpause()` on all three contracts. `highestRsethPrice` remains `P_high`.
5. Attacker immediately calls `updateRSETHPrice()` (oracle is now unpaused, so `whenNotPaused` passes).
6. `_updateRsETHPrice()` evaluates `P_low < P_high` by more than the limit → re-pauses oracle, deposit pool, and withdrawal manager.
7. Steps 4–6 repeat indefinitely. Users cannot deposit or withdraw. The attacker spends only gas.

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L143-146)
```text
    function unpause() external whenPaused onlyLRTAdmin {
        paused = false;
        emit Unpaused(msg.sender);
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

**File:** contracts/LRTOracle.sol (L294-296)
```text
        if (newRsETHPrice > highestRsethPrice) {
            highestRsethPrice = newRsETHPrice;
        }
```
