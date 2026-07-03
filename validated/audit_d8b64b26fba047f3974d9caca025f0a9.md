Audit Report

## Title
Unrestricted `updateRSETHPrice()` Allows Any Caller to Trigger Protocol-Wide Pause — (File: contracts/LRTOracle.sol)

## Summary
`LRTOracle.updateRSETHPrice()` is declared `public` with only a `whenNotPaused` guard and no role restriction. Any unprivileged EOA can call it. When the computed rsETH price has dropped more than `pricePercentageLimit` below `highestRsethPrice`, the function pauses `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle` itself, immediately blocking all user deposits and withdrawals. Unpausing requires `onlyLRTAdmin`, so users remain frozen until the admin acts.

## Finding Description
`updateRSETHPrice()` at line 87 carries no role modifier, while its privileged sibling `updateRSETHPriceAsManager()` at line 94 is correctly gated with `onlyLRTManager`. Both delegate to `_updateRsETHPrice()`.

Inside `_updateRsETHPrice()`, the downside-protection branch at lines 270–282 evaluates:
```solidity
bool isPriceDecreaseOffLimit =
    pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

if (isPriceDecreaseOffLimit) {
    if (!lrtDepositPool.paused()) lrtDepositPool.pause();
    if (!withdrawalManager.paused()) withdrawalManager.pause();
    _pause();
    return;
}
```
When this condition is true, the function pauses all three contracts and returns. Because `updateRSETHPrice()` is `public` with no access control, any EOA can invoke this path the moment market conditions satisfy `isPriceDecreaseOffLimit`. The `onlyLRTManager` check inside `_updateRsETHPrice()` at line 263 only applies to the *price-increase* branch; the price-decrease/pause branch has no equivalent role check.

The fee-minting side-effect (lines 299–308) is also reachable by any caller. However, the minted rsETH goes to the protocol treasury — the intended recipient — so this does not constitute theft of user funds or yield; it is the protocol's own fee mechanism triggered at an attacker-chosen time, bounded by `maxFeeMintAmountPerDay`. This secondary path does not independently satisfy an allowed High impact.

## Impact Explanation
**Temporary freezing of funds (Medium).** An attacker who calls `updateRSETHPrice()` when `isPriceDecreaseOffLimit` is satisfied causes `LRTDepositPool.paused() == true` and `LRTWithdrawalManager.paused() == true`. All user deposit and withdrawal transactions revert until an admin calls `unpause()` on each contract. The attacker bears only gas cost and can repeat the attack after each admin unpause if market conditions persist.

## Likelihood Explanation
The function is `public` on a deployed, verified contract. No capital, privilege, or special setup is required. The condition `isPriceDecreaseOffLimit` arises naturally during LST slashing events, sustained depegs, or periods of market stress — exactly the moments when users most need to withdraw. Any EOA can monitor on-chain oracle prices and call the function the instant the threshold is crossed, before any legitimate operator does.

## Recommendation
Apply `onlyLRTOperator` (or `onlyLRTManager`, consistent with `updateRSETHPriceAsManager`) to `updateRSETHPrice()`:

```solidity
- function updateRSETHPrice() public whenNotPaused {
+ function updateRSETHPrice() external whenNotPaused onlyLRTOperator {
      _updateRsETHPrice();
  }
```

This mirrors the access pattern already enforced on every other state-mutating function in the protocol via `LRTConfigRoleChecker`.

## Proof of Concept
1. Deploy or identify the live `LRTOracle` proxy with `pricePercentageLimit > 0`.
2. Wait until (or simulate) a market condition where the current LST oracle prices yield a `newRsETHPrice` satisfying `highestRsethPrice - newRsETHPrice > pricePercentageLimit.mulWad(highestRsethPrice)`.
3. From any EOA (no roles), call `LRTOracle.updateRSETHPrice()`.
4. Assert:
   - `LRTDepositPool.paused() == true`
   - `LRTWithdrawalManager.paused() == true`
   - `LRTOracle.paused == true`
   - Any subsequent user deposit or withdrawal reverts.
5. Confirm that unpausing requires a privileged admin call, leaving users frozen in the interim.

**Foundry fork test sketch:**
```solidity
function test_anyCallerCanPauseProtocol() public {
    // manipulate mock oracle to drop price below threshold
    mockOracle.setPrice(highestRsethPrice * (1e18 - pricePercentageLimit - 1e14) / 1e18);
    vm.prank(address(0xdead)); // unprivileged EOA
    lrtOracle.updateRSETHPrice();
    assertTrue(lrtDepositPool.paused());
    assertTrue(withdrawalManager.paused());
    assertTrue(lrtOracle.paused());
}
```