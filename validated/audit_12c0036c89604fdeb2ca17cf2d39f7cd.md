### Title
Unpause Does Not Reset `highestRsethPrice`, Allowing Any Caller to Immediately Re-Trigger Auto-Pause — (`contracts/LRTOracle.sol`)

---

### Summary

`unpause()` only flips `paused = false` without resetting `highestRsethPrice`. Because `updateRSETHPrice()` is public and permissionless, any caller can immediately re-trigger the auto-pause after an admin unpause by calling it with the same (still-depressed) oracle prices, keeping the protocol frozen indefinitely.

---

### Finding Description

`_updateRsETHPrice()` contains a downside-protection circuit breaker: [1](#0-0) 

When `newRsETHPrice < highestRsethPrice` by more than `pricePercentageLimit`, the function pauses the oracle, deposit pool, and withdrawal manager, then **returns early** — leaving both `rsETHPrice` and `highestRsethPrice` unchanged.

The `unpause()` function only resets the `paused` flag: [2](#0-1) 

It does **not** update `highestRsethPrice` to the current price, nor does it update `rsETHPrice`. After `unpause()`, the state is identical to the moment before the auto-pause fired, except `paused = false`.

`updateRSETHPrice()` is public with only a `whenNotPaused` guard: [3](#0-2) 

So the attack loop is:

1. Oracle prices drop → anyone calls `updateRSETHPrice()` → auto-pause fires, `highestRsethPrice` stays at peak.
2. Admin calls `unpause()` → `paused = false`, `highestRsethPrice` still at peak.
3. Attacker calls `updateRSETHPrice()` → same oracle prices → same `newRsETHPrice < highestRsethPrice` → `isPriceDecreaseOffLimit = true` → `_pause()` fires again.
4. Repeat from step 2 indefinitely.

`updateRSETHPriceAsManager()` does not help break the cycle: it also calls `_updateRsETHPrice()` and the downside auto-pause path has no manager bypass — the manager role only bypasses the **upside** threshold check at line 263. [4](#0-3) 

---

### Impact Explanation

Every admin unpause can be immediately nullified by any EOA calling the public `updateRSETHPrice()`. Deposits, withdrawals, and oracle updates remain frozen for as long as oracle prices stay below `highestRsethPrice * (1 - pricePercentageLimit)`. This constitutes **temporary (but indefinitely extendable) freezing of user funds**, matching the Medium impact scope.

---

### Likelihood Explanation

- `pricePercentageLimit` must be non-zero (it is a configurable admin parameter, expected to be set in production).
- The auto-pause fires precisely when prices are depressed; they are unlikely to recover instantly, so the condition persists across the unpause.
- The attacker needs no funds, no role, and no front-running — a single public call suffices.
- The attack is repeatable at zero cost (only gas).

---

### Recommendation

In `unpause()`, reset `highestRsethPrice` to the current computed price (or to `rsETHPrice`) so that the next call to `updateRSETHPrice()` uses the post-recovery baseline rather than the stale historical peak:

```solidity
function unpause() external whenPaused onlyLRTAdmin {
    paused = false;
    highestRsethPrice = rsETHPrice; // reset baseline to current price
    emit Unpaused(msg.sender);
}
```

Alternatively, require the manager to call `updateRSETHPriceAsManager()` (which can execute while paused) to update `highestRsethPrice` before the admin unpauses, and add a downside-bypass for the manager role symmetric to the existing upside bypass.

---

### Proof of Concept

Fork-test sequence (no mainnet, local fork):

```solidity
// 1. Setup: pricePercentageLimit = 1e16 (1%), prices set so newRsETHPrice < highestRsethPrice by >1%
// 2. Anyone calls updateRSETHPrice() → auto-pause fires
assert(oracle.paused() == true);
assert(oracle.highestRsethPrice() == PEAK); // unchanged

// 3. Admin unpauses
vm.prank(admin);
oracle.unpause();
assert(oracle.paused() == false);
assert(oracle.highestRsethPrice() == PEAK); // still unchanged

// 4. Attacker immediately calls updateRSETHPrice() — same oracle prices, no state change needed
vm.prank(attacker);
oracle.updateRSETHPrice();

// 5. Protocol is paused again
assert(oracle.paused() == true); // re-paused, admin unpause was nullified
assert(oracle.highestRsethPrice() == PEAK); // still unchanged
```

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
