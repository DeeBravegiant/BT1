Looking at the code carefully to validate this claim.

Audit Report

## Title
Stale `rsETHPrice` Used in Deposit Minting Allows Depositors to Capture Accrued Yield - (`contracts/LRTDepositPool.sol`)

## Summary
`LRTDepositPool.depositETH` and `depositAsset` mint rsETH using the stored `LRTOracle.rsETHPrice`, which is never refreshed atomically before a deposit. When staking rewards have accrued since the last `updateRSETHPrice()` call, the stored price is lower than the true price, causing depositors to receive more rsETH than they are entitled to. This dilutes the yield of all pre-existing rsETH holders.

## Finding Description
`LRTOracle.rsETHPrice` is a state variable updated only by explicit calls to `updateRSETHPrice()` (L87-89) or `updateRSETHPriceAsManager()` (L94-96). Neither is invoked anywhere in the deposit path.

`depositETH` and `depositAsset` both call `_beforeDeposit` (L87, L111), which calls `getRsETHAmountToMint` (L665). That function computes:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
(`LRTDepositPool.sol` L520)

`lrtOracle.getAssetPrice(asset)` is a live oracle read, but `lrtOracle.rsETHPrice()` returns the last-written storage value. As EigenLayer/LST staking rewards accrue, the true rsETH price (`totalETHInProtocol / rsethSupply`) rises while `rsETHPrice` remains frozen. Because the divisor is stale-low, the quotient — the rsETH minted — is inflated above what the depositor deserves.

The `_updateRsETHPrice()` logic (L214-316) also contains a `pricePercentageLimit` guard (L252-266): if the price increase exceeds the configured threshold, `updateRSETHPrice()` reverts for non-managers (`PriceAboveDailyThreshold`). This can extend the staleness window further, since only a manager can then push the price forward, while deposits continue using the frozen (lower) price throughout.

No existing check in `_beforeDeposit` or `getRsETHAmountToMint` validates the freshness of `rsETHPrice` or bounds the minted amount against a live TVL calculation.

## Impact Explanation
**High — Theft of unclaimed yield.**

Every rsETH holder's proportional claim on protocol TVL is diluted. The depositor receives rsETH backed by more ETH than they contributed; the shortfall is borne by pre-existing holders whose share of the TVL shrinks. The magnitude scales with (a) the elapsed time since the last `updateRSETHPrice()` call and (b) the staking yield accrued in that window. The `pricePercentageLimit` guard can extend the staleness window, amplifying the impact.

## Likelihood Explanation
`updateRSETHPrice()` is a public function with no keeper enforcement on-chain. Any gap in off-chain bot operation (network congestion, downtime, or the `pricePercentageLimit` revert blocking non-manager callers) creates an exploitable window. Any unprivileged depositor benefits automatically during such a window; no special capability is required. The attack is repeatable across every staleness window.

## Recommendation
Call `_updateRsETHPrice()` (or an equivalent internal refresh) at the start of `depositETH` and `depositAsset`, before `_beforeDeposit` computes `rsethAmountToMint`. This ensures the price used for minting always reflects the current TVL. If the gas cost of a full price update on every deposit is unacceptable, a maximum-staleness check (e.g., revert if `rsETHPrice` has not been updated within N blocks) is a viable alternative.

## Proof of Concept
1. Staking rewards accrue for several hours; `updateRSETHPrice()` has not been called. True rsETH price is 1.005 ETH but stored `rsETHPrice` is 1.000 ETH.
2. Attacker calls `depositETH{value: 100 ETH}(0, "")`.
   - `getRsETHAmountToMint` computes `100e18 * 1e18 / 1.000e18 = 100 rsETH`.
   - Correct amount: `100e18 * 1e18 / 1.005e18 ≈ 99.502 rsETH`.
   - Attacker receives ~0.498 rsETH excess.
3. Anyone calls `updateRSETHPrice()`. `rsETHPrice` updates to 1.005 ETH.
4. Attacker holds 100 rsETH now worth `100 × 1.005 = 100.5 ETH` — a ~0.5 ETH profit funded by diluting existing holders.

**Foundry fork test outline:**
```solidity
// 1. Fork mainnet at block B where rsETHPrice = P_stale
// 2. Roll forward N blocks to simulate reward accrual (true price P_true > P_stale)
// 3. Record existing holder's share value = holderRsETH * P_stale
// 4. Attacker deposits 100 ETH; record rsethMinted
// 5. Call updateRSETHPrice(); record new price P_new
// 6. Assert rsethMinted > 100e18 * 1e18 / P_new  (attacker got excess)
// 7. Assert existing holder's share value post-update < pre-deposit value (dilution)
```