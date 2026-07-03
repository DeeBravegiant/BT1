Audit Report

## Title
`highestRsethPrice` Not Reset on `unpause()` Enables Permissionless Re-Pause Loop — (File: `contracts/LRTOracle.sol`)

## Summary
`LRTOracle.unpause()` resets only the `paused` flag but never resets `highestRsethPrice`. Because `_updateRsETHPrice()` auto-pauses the protocol whenever the current price is below `highestRsethPrice` by more than `pricePercentageLimit`, any unprivileged caller can immediately re-pause the oracle, deposit pool, and withdrawal manager after an admin unpause by calling the public `updateRSETHPrice()`. This renders the admin's unpause action ineffective for as long as the price has not recovered, constituting a temporary freezing of user funds.

## Finding Description
`updateRSETHPrice()` is public and gated only by `whenNotPaused`: [1](#0-0) 

Inside `_updateRsETHPrice()`, when the new price is below `highestRsethPrice` by more than `pricePercentageLimit`, the function pauses the oracle, deposit pool, and withdrawal manager, then returns early without updating `highestRsethPrice`: [2](#0-1) 

`highestRsethPrice` is only ever updated upward: [3](#0-2) 

`unpause()` resets only `paused = false` and never touches `highestRsethPrice`: [4](#0-3) 

After the admin unpauses, `whenNotPaused` passes again, so any caller can invoke `updateRSETHPrice()`. `_updateRsETHPrice()` re-evaluates the same stale `highestRsethPrice` against the still-depressed current price, the condition `diff > pricePercentageLimit.mulWad(highestRsethPrice)` is still true, and the protocol is re-paused in the same transaction. The cycle repeats indefinitely at the cost of only gas.

The `updateRSETHPriceAsManager()` escape hatch does not help here — it also calls `_updateRsETHPrice()` and would equally trigger the auto-pause if the price has not recovered: [5](#0-4) 

The admin's only on-chain mitigation without a contract upgrade is to call `setPricePercentageLimit(0)` before unpausing, which disables the downside-protection mechanism entirely and introduces its own risk.

## Impact Explanation
While the oracle is paused, `LRTDepositPool` and `LRTWithdrawalManager` are also paused, preventing all deposits and withdrawals. Any unprivileged caller can sustain this state indefinitely after each admin unpause by spending only gas. This constitutes **Medium — Temporary freezing of funds**: user funds are not lost, but access is blocked for an extended and attacker-controlled duration until the price recovers or the admin disables `pricePercentageLimit`.

## Likelihood Explanation
The initial trigger requires a genuine price drop exceeding `pricePercentageLimit` relative to `highestRsethPrice`, a realistic market event for a liquid restaking token. Once triggered, sustaining the freeze requires no capital, no role, and no special access — only gas per block. Any address can call `updateRSETHPrice()` after each admin unpause. The cost to the attacker is negligible; the cost to users is indefinite loss of access to deposits and withdrawals.

## Recommendation
Reset `highestRsethPrice` to the current `rsETHPrice` inside `unpause()` so the auto-pause threshold is recalibrated to the post-recovery baseline:

```solidity
function unpause() external whenPaused onlyLRTAdmin {
    paused = false;
    highestRsethPrice = rsETHPrice; // recalibrate peak to current price
    emit Unpaused(msg.sender);
}
```

Alternatively, add a dedicated `onlyLRTAdmin` setter for `highestRsethPrice` so the admin can reset it independently of the pause state.

## Proof of Concept
1. Normal operation sets `highestRsethPrice = P_high`.
2. rsETH price drops to `P_low` where `P_high − P_low > pricePercentageLimit × P_high`.
3. Anyone calls `updateRSETHPrice()` → `_updateRsETHPrice()` auto-pauses oracle, deposit pool, and withdrawal manager. `highestRsethPrice` remains `P_high`.
4. Admin calls `unpause()` on all three contracts. `highestRsethPrice` is still `P_high`; `rsETHPrice` is still `P_low`.
5. Attacker immediately calls `updateRSETHPrice()` (oracle is now unpaused, so `whenNotPaused` passes).
6. `_updateRsETHPrice()` evaluates `P_low < P_high` by more than the limit → re-pauses oracle, deposit pool, and withdrawal manager.
7. Steps 4–6 repeat indefinitely. Users cannot deposit or withdraw.

**Foundry test plan:** Deploy `LRTOracle` with a mock `LRTConfig`, mock `LRTDepositPool`, and mock `LRTWithdrawalManager`. Set `pricePercentageLimit` to 1e16 (1%). Seed `highestRsethPrice = 1.05 ether` and `rsETHPrice = 1.0 ether` (>1% drop). Assert `updateRSETHPrice()` pauses all three contracts. Call `unpause()` as admin. Assert `paused == false`. Call `updateRSETHPrice()` as an unprivileged address. Assert all three contracts are paused again. Repeat the unpause/re-pause cycle N times to confirm the loop is unbounded.

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
