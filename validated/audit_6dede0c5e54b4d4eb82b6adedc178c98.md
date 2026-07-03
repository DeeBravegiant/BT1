Audit Report

## Title
Stale `rsETHPrice` in `getRsETHAmountToMint()` Allows Depositors to Receive Excess rsETH, Diluting Existing Holders' Yield - (File: contracts/LRTDepositPool.sol)

## Summary
`getRsETHAmountToMint()` divides by `lrtOracle.rsETHPrice()`, a cached state variable in `LRTOracle` that is only updated via explicit calls to `updateRSETHPrice()`. As staking rewards accrue between updates, the stored price falls below the true price, causing any depositor to receive more rsETH than their contribution warrants. The excess rsETH permanently dilutes existing holders' share of the TVL, constituting theft of unclaimed yield.

## Finding Description
`getRsETHAmountToMint()` at `LRTDepositPool.sol:520` computes:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`lrtOracle.getAssetPrice(asset)` fetches a live price, but `lrtOracle.rsETHPrice()` returns the stored state variable `rsETHPrice` in `LRTOracle` (`LRTOracle.sol:28`), which is only updated when `_updateRsETHPrice()` is called internally via `updateRSETHPrice()` (`LRTOracle.sol:87-89`) or `updateRSETHPriceAsManager()` (`LRTOracle.sol:94-96`).

Neither `depositETH()` (`LRTDepositPool.sol:76-93`) nor `depositAsset()` calls `updateRSETHPrice()` before invoking `_beforeDeposit()` (`LRTDepositPool.sol:648-670`), which itself is `private view` and cannot update state. The minting amount is therefore always computed against the last stored price, not the current true price.

As ETH staking rewards accrue, `totalETHInProtocol` grows while `rsethSupply` and `rsETHPrice` remain unchanged. The true price (`totalETHInProtocol / rsethSupply`) rises above `rsETHPrice`. A depositor who deposits during this window receives:

```
rsethMinted = deposit * assetPrice / rsETHPrice_stale  >  deposit * assetPrice / rsETHPrice_true
```

When `updateRSETHPrice()` is eventually called, the new price is computed as `(totalETHInProtocol - fee) / rsethSupply` (`LRTOracle.sol:250`). Because `rsethSupply` is inflated by the excess minted tokens, the settled price is permanently lower than it would have been, diluting every existing holder.

An additional compounding factor: when the price increase since the last update exceeds `pricePercentageLimit`, `updateRSETHPrice()` reverts for non-manager callers (`LRTOracle.sol:260-265`), creating enforced windows of staleness during which the attack is guaranteed to succeed for any depositor.

## Impact Explanation
**High — Theft of unclaimed yield.** Accrued staking rewards that belong to existing rsETH holders are transferred to new depositors through inflated rsETH minting. The dilution is permanent: once the excess rsETH is minted and `updateRSETHPrice()` is called, the settled price is lower than it should be, and existing holders cannot recover the stolen yield. This matches the allowed impact "High. Theft of unclaimed yield."

## Likelihood Explanation
**Medium.** ETH staking rewards accrue every block, so `rsETHPrice` is perpetually stale between updates. No special setup is required — any depositor calling `depositETH()` or `depositAsset()` during a staleness window benefits. Depositors have a direct financial incentive to avoid calling `updateRSETHPrice()` before depositing. The `pricePercentageLimit` guard can extend the exploitable window further. The exploit is repeatable and scales with deposit size and staleness duration.

## Recommendation
Call `_updateRsETHPrice()` atomically at the start of `_beforeDeposit()` (removing the `view` modifier) before computing `getRsETHAmountToMint()`, so the rsETH price always reflects the current TVL and supply at the moment of deposit. Alternatively, compute the rsETH price on-the-fly from live TVL and supply inside `getRsETHAmountToMint()` rather than reading the cached state variable.

## Proof of Concept
**Setup:**
- Protocol TVL: 100 ETH, rsETH supply: 100, `rsETHPrice` = 1e18
- 1 ETH of staking rewards accrue → true price = 101/100 = 1.01e18
- `updateRSETHPrice()` is NOT called

**Steps:**
1. Attacker calls `depositETH{ value: 1 ether }(0, "")`.
2. `getRsETHAmountToMint(ETH, 1e18)` computes `1e18 * 1e18 / 1e18 = 1e18` → mints **1 rsETH**.
3. Correct amount at true price: `1e18 * 1e18 / 1.01e18 ≈ 0.9901e18` rsETH.
4. Attacker received **~0.0099 rsETH excess**.

**After `updateRSETHPrice()` is called:**
- TVL = 102 ETH, supply = 101 rsETH → settled price = 102/101 ≈ 1.0099e18
- Correct price (if attacker got 0.9901 rsETH): 102/100.9901 ≈ 1.0100e18
- Existing holders' rsETH is worth ~0.01% less than it should be — the attacker extracted that yield.

**Foundry fork test plan:** Fork mainnet, record `rsETHPrice` and `totalETHInProtocol`, advance time by 1 day (to accrue rewards), call `depositETH` without calling `updateRSETHPrice` first, then call `updateRSETHPrice`, and assert that the settled price is lower than `(totalETHInProtocol_after_rewards - fee) / rsethSupply_before_deposit`.