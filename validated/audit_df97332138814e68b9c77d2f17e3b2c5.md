Audit Report

## Title
Cross-Asset Withdrawal Allows Attacker to Socialize LST Slashing Losses onto rsETH Holders - (`contracts/LRTWithdrawalManager.sol` / `contracts/LRTDepositPool.sol`)

## Summary
`LRTDepositPool.depositAsset()` mints rsETH based on the deposited asset's price relative to the rsETH basket price. Because rsETH is a basket-weighted average, a drop in one LST reduces rsETH price by only `weight × drop`, not the full drop. An attacker who deposits a depreciating LST before the price drop and then calls `initiateWithdrawal()` for a different asset (e.g., ETH) after the drop recovers more ETH than their LST is currently worth, transferring the difference to other rsETH holders. The `_calculatePayoutAmount` cap reduces but does not eliminate the profit because the cap is computed against the already-reduced rsETH price, not the original deposited asset value.

## Finding Description

**Deposit path** (`LRTDepositPool.sol` L519–520):
```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
Minting is priced at the current basket rsETH price. If stETH is 50% of the basket and drops 10%, rsETH price drops only ~6.77%, so the attacker's rsETH retains more value than their stETH.

**Withdrawal path** (`LRTWithdrawalManager.sol` L168, L593):
```solidity
uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
// ...
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```
`initiateWithdrawal()` accepts *any* supported asset as the withdrawal target with no link to the deposited asset. The attacker specifies ETH (price always 1.0 ETH) as the withdrawal asset after the stETH drop.

**Payout cap** (`LRTWithdrawalManager.sol` L833–834):
```solidity
uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```
At unlock time, `currentReturn` is computed against the *post-drop* rsETH price. Since rsETH dropped less than stETH (basket dilution), `currentReturn` is still greater than the stETH's current ETH value. The cap does not close the gap.

**Attack does not require advance prediction.** The attacker only needs to have deposited stETH *before* the drop. After the drop occurs, they can immediately call `initiateWithdrawal(ETH, rsETHBalance)` — the profit is already locked in by the basket pricing asymmetry.

**`pricePercentageLimit` guard** (`LRTOracle.sol` L270–281): Pauses the protocol only if rsETH price drops beyond the configured threshold. For drops within the limit (or if the limit is unset), the attack proceeds unimpeded. Even for larger drops, once the pause is lifted, the attacker can initiate withdrawal at the still-depressed rsETH price.

## Impact Explanation
**Critical — Direct theft of user funds.** Other rsETH holders' principal is reduced by the attacker's extracted surplus. In the PoC, 3.39 ETH is transferred from existing rsETH holders to the attacker on a 205 ETH TVL pool. The loss scales linearly with deposit size and is repeatable by any holder of a depreciating LST. This is a direct, quantifiable reduction in the ETH value backing other users' rsETH — matching "Direct theft of any user funds, whether at-rest or in-motion."

## Likelihood Explanation
**Medium.** The attacker only needs to:
1. Hold any supported LST and have deposited it into the protocol prior to a slashing/price-drop event (a normal depositor action).
2. React to the price drop by calling `initiateWithdrawal(ETH, ...)` — no advance prediction required.
3. Wait the 8-day `withdrawalDelayBlocks` window.

LST slashing events (e.g., Ethereum validator slashing) are publicly observable on-chain and unfold over days, giving ample time to react. The attack is repeatable and requires no privileged access.

## Recommendation
1. **Restrict withdrawal asset to deposited asset**: Track `depositedAsset[user]` and enforce that `initiateWithdrawal` can only target the same asset.
2. **Snapshot rsETH price at deposit time**: Use the deposit-time rsETH price as the withdrawal price floor, preventing basket-dilution arbitrage.
3. **Per-asset withdrawal queues with cross-asset checks**: Ensure `assetsCommitted` accounting prevents a user from committing ETH based on a stETH deposit.
4. **Tighten `pricePercentageLimit`**: Set it low enough to pause the protocol before the basket-dilution gap becomes exploitable.

## Proof of Concept

**Setup**: Protocol holds 100 ETH + 100 stETH (at 1.05 ETH/stETH). TVL = 205 ETH. rsETH supply = 205. rsETH price = 1.0 ETH.

**Step 1 — Attacker deposits 100 stETH** (normal depositor action, no prediction needed):
- `rsETH minted = (100 × 1.05) / 1.0 = 105 rsETH`
- New TVL = 310 ETH, rsETH price = 1.0 ETH

**Step 2 — stETH slashing event occurs** (publicly observable):
- stETH price drops 10% → 0.945 ETH/stETH
- New TVL = 100 + 200 × 0.945 = 289 ETH
- `updateRSETHPrice()` → rsETH price = 289/310 ≈ 0.9323 ETH (dropped only 6.77%)

**Step 3 — Attacker calls `initiateWithdrawal(ETH, 105)`** (reacting to the drop):
- `expectedAssetAmount = 105 × 0.9323 / 1.0 ≈ 97.89 ETH` (locked in)
- `assetsCommitted[ETH] += 97.89`

**Step 4 — After 8 days, operator calls `unlockQueue(ETH, ...)`**:
- `currentReturn = 105 × 0.9323 / 1.0 ≈ 97.89 ETH`
- `payout = min(97.89, 97.89) = 97.89 ETH`

**Result**:
- Attacker receives **97.89 ETH**
- Attacker's 100 stETH is worth **94.5 ETH** (100 × 0.945)
- **Attacker profit: ~3.39 ETH** extracted from other rsETH holders
- Original 205 rsETH holders now hold 191.11 ETH instead of 194.5 ETH — a **3.39 ETH loss** they did not cause

**Foundry test plan**: Deploy protocol with two assets (ETH, stETH). Deposit 100 ETH + 100 stETH as background liquidity. Attacker deposits 100 stETH. Mock oracle to drop stETH price 10%. Attacker calls `initiateWithdrawal(ETH, 105e18)`. Advance blocks by `withdrawalDelayBlocks`. Call `unlockQueue`. Assert attacker receives > 94.5 ETH and background rsETH holders' pro-rata ETH value decreased by the same amount.