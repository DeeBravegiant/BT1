### Title
Unauthenticated `updateRSETHPrice()` Allows Any Caller to Trigger Protocol-Wide Pause — (File: contracts/LRTOracle.sol)

### Summary
`LRTOracle.updateRSETHPrice()` is a `public` function with no access control. Any external caller can invoke it at any time. When the computed rsETH price has fallen below `highestRsethPrice` by more than `pricePercentageLimit`, the function unconditionally pauses `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle` itself. An unprivileged attacker can exploit this to force a protocol-wide pause at a strategically chosen moment, temporarily freezing all user deposits and withdrawals.

### Finding Description
`updateRSETHPrice()` is declared `public whenNotPaused` with no role check:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

Inside `_updateRsETHPrice()`, the downside-protection branch executes unconditionally for any caller:

```solidity
if (isPriceDecreaseOffLimit) {
    if (!lrtDepositPool.paused()) lrtDepositPool.pause();
    if (!withdrawalManager.paused()) withdrawalManager.pause();
    _pause();
    return;
}
```

The privileged counterpart `updateRSETHPriceAsManager()` exists precisely because managers need to update the price in situations where the public function would revert or pause. The public function has no equivalent guard — it will pause the entire protocol the moment the price condition is met, regardless of who calls it.

### Impact Explanation
When the attacker's call triggers the pause:
- `LRTDepositPool` is paused → no new deposits of ETH or LSTs.
- `LRTWithdrawalManager` is paused → no withdrawal completions or instant withdrawals.
- `LRTOracle` itself is paused → no further price updates via the public path.

All user funds in flight (pending withdrawals, queued deposits) are frozen until an admin with `onlyLRTAdmin` calls `unpause()` on each contract. This constitutes **temporary freezing of funds** (Medium impact per scope).

### Likelihood Explanation
The price condition (`newRsETHPrice < highestRsethPrice` by more than `pricePercentageLimit`) can arise from:
1. **Natural market movement**: any dip in an underlying LST oracle price (stETH, ETHx, etc.) reduces `totalETHInProtocol` and thus `newRsETHPrice`.
2. **Operator timing**: the protocol manager may intentionally delay calling `updateRSETHPriceAsManager()` while waiting for a transient dip to recover. During that window, any external caller can call `updateRSETHPrice()` and force the pause the manager was trying to avoid.
3. **Oracle latency**: brief oracle price lags are common; an attacker monitoring on-chain oracle values can time the call to the exact block where the condition is satisfied.

No special privileges, tokens, or capital are required. The attacker only needs to submit a transaction.

### Recommendation
Restrict `updateRSETHPrice()` to a permissioned role (e.g., `onlyLRTOperator` or `onlyLRTManager`), or add a separate unprivileged path that explicitly cannot trigger the pause branch (only the manager path should be allowed to update price when the pause threshold is crossed). The current design conflates "anyone can refresh the price" with "anyone can pause the protocol."

### Proof of Concept
1. Observe that a supported LST oracle price has dipped such that the computed `newRsETHPrice` would be below `highestRsethPrice * (1 - pricePercentageLimit)`.
2. Call `LRTOracle.updateRSETHPrice()` from any EOA.
3. `_updateRsETHPrice()` computes `newRsETHPrice`, enters the `isPriceDecreaseOffLimit` branch, and calls `lrtDepositPool.pause()`, `withdrawalManager.pause()`, and `_pause()`.
4. All deposits and withdrawals are now frozen. The manager must manually unpause each contract via `onlyLRTAdmin`-gated `unpause()` calls.

Relevant code: [1](#0-0) [2](#0-1) [3](#0-2)

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
