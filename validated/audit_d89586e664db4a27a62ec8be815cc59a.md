### Title
Unprotected `updateRSETHPrice()` Enables Any Caller to Trigger Protocol-Wide Pause - (File: contracts/LRTOracle.sol)

---

### Summary
`LRTOracle.updateRSETHPrice()` is declared `public whenNotPaused` with no access control. Any external address can invoke it at any time. When the computed rsETH price has fallen by more than `pricePercentageLimit` from `highestRsethPrice`, the function automatically pauses `LRTDepositPool`, `LRTWithdrawalManager`, and the oracle itself, temporarily freezing all user deposits and withdrawals.

---

### Finding Description

`LRTOracle.sol` exposes two entry points into the same internal `_updateRsETHPrice()` logic:

```solidity
// line 87 — no access control
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}

// line 94 — restricted to manager
function updateRSETHPriceAsManager() external onlyLRTManager {
    _updateRsETHPrice();
}
```

The existence of `updateRSETHPriceAsManager()` with `onlyLRTManager` demonstrates the protocol's own recognition that price updates carry privileged consequences. Yet `updateRSETHPrice()` imposes no such restriction.

Inside `_updateRsETHPrice()`, the downside-protection branch at lines 270–282 reads:

```solidity
if (newRsETHPrice < highestRsethPrice) {
    uint256 diff = highestRsethPrice - newRsETHPrice;
    bool isPriceDecreaseOffLimit =
        pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

    if (isPriceDecreaseOffLimit) {
        if (!lrtDepositPool.paused()) lrtDepositPool.pause();
        if (!withdrawalManager.paused()) withdrawalManager.pause();
        _pause();
        return;
    }
}
```

When the on-chain price has genuinely dropped by more than `pricePercentageLimit` (e.g., due to slashing, LST de-peg, or EigenLayer strategy losses), any unprivileged address can race to call `updateRSETHPrice()` before the protocol team has had a chance to assess the situation. The call succeeds, pauses all three contracts, and returns — with no way for the caller to be blocked.

The upside branch (lines 252–266) does check `msg.sender` for the manager role before allowing a price increase above the threshold, but the downside branch has no such caller check, making the asymmetry exploitable.

---

### Impact Explanation

**Temporary freezing of funds (Medium).**

Once the pause is triggered:
- `LRTDepositPool` rejects all `depositETH` and `depositAsset` calls.
- `LRTWithdrawalManager` rejects all withdrawal claims.
- `LRTOracle` itself is paused, blocking further price updates via the public path.

Unpausing requires `onlyLRTAdmin` for each contract. Until the admin acts, all user funds are inaccessible. The attacker incurs only gas cost and gains no direct financial benefit, but the disruption is real and repeatable whenever the price condition is met.

---

### Likelihood Explanation

**Medium.** The price condition (`newRsETHPrice < highestRsethPrice` by more than `pricePercentageLimit`) is a realistic market event — slashing of an EigenLayer operator, a temporary LST de-peg, or a reduction in underlying strategy balances can all produce it. An attacker monitoring on-chain oracle prices can detect the condition and front-run the protocol team's own update call, triggering the pause before any human review occurs. No privileged access, no capital, and no oracle manipulation is required.

---

### Recommendation

Apply the same access control to `updateRSETHPrice()` that is already applied to `updateRSETHPriceAsManager()`, or restrict the automatic pause trigger to calls originating from an authorized role:

```solidity
// Option A: restrict the public entry point
function updateRSETHPrice() public whenNotPaused onlyLRTOperator {
    _updateRsETHPrice();
}

// Option B: keep it public but gate the pause branch
if (isPriceDecreaseOffLimit) {
    if (IAccessControl(address(lrtConfig)).hasRole(LRTConstants.OPERATOR_ROLE, msg.sender)
        || IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
        // pause
    } else {
        revert PriceBelowDailyThreshold();
    }
}
```

---

### Proof of Concept

1. Observe that the on-chain rsETH price (computed from `_getTotalEthInProtocol() / rsethSupply`) has fallen by more than `pricePercentageLimit` relative to `highestRsethPrice` (e.g., due to a slashing event).
2. Call `LRTOracle.updateRSETHPrice()` from any EOA — no role required.
3. `_updateRsETHPrice()` computes `newRsETHPrice`, enters the `isPriceDecreaseOffLimit` branch, and calls `lrtDepositPool.pause()`, `withdrawalManager.pause()`, and `_pause()`.
4. All deposits and withdrawals are frozen. Only `onlyLRTAdmin` can unpause each contract individually.
5. The attack is repeatable after each unpause as long as the price condition persists. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** contracts/LRTOracle.sol (L269-282)
```text
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
