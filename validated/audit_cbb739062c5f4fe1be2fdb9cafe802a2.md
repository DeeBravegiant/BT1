Audit Report

## Title
Depositors Mint rsETH at Stale Price, Diluting Existing Holders' Accrued Yield — (File: contracts/LRTDepositPool.sol)

## Summary
`LRTDepositPool.getRsETHAmountToMint()` divides by the stored `rsETHPrice` state variable from `LRTOracle`, which is only updated on explicit calls to `updateRSETHPrice()`. As staking rewards accrue and the true protocol TVL rises, the stored price lags behind, allowing any depositor to mint more rsETH than the true exchange rate warrants. When the price is subsequently updated, the attacker's rsETH is worth more than they paid, at the direct expense of pre-existing holders' accrued yield.

## Finding Description
`getRsETHAmountToMint()` computes:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`lrtOracle.rsETHPrice()` returns the stored state variable `rsETHPrice` (LRTOracle.sol L28), which is only mutated inside `_updateRsETHPrice()` (LRTOracle.sol L313). The public entry point `updateRSETHPrice()` (LRTOracle.sol L87–89) is permissionless but not called atomically with deposits.

`_updateRsETHPrice()` computes the true price as `(totalETHInProtocol - protocolFeeInETH) / rsethSupply` (LRTOracle.sol L250), where `totalETHInProtocol` is derived from `_getTotalEthInProtocol()` (LRTOracle.sol L331–349), which aggregates `getTotalAssetDeposits()` across all supported assets — a value that grows as EigenLayer staking rewards accrue into NodeDelegator balances. Between calls to `updateRSETHPrice()`, the stored `rsETHPrice` is frozen while the true TVL rises, creating a staleness window.

`_beforeDeposit()` (LRTDepositPool.sol L648–670) performs no freshness check on `rsETHPrice` before calling `getRsETHAmountToMint()`. The `pricePercentageLimit` guard (LRTOracle.sol L252–266) only restricts non-manager callers from *applying* a price update above the threshold; it does not block deposits at the stale price and can actually extend the staleness window by reverting public `updateRSETHPrice()` calls when accumulated rewards exceed the threshold.

**Exploit path:**
1. Staking rewards accrue; true TVL rises above `rsETHPrice × totalSupply`. Stored `rsETHPrice` is now below the true price.
2. Attacker calls `depositETH()` or `depositAsset()`. The stale denominator yields more rsETH than the true rate warrants.
3. Anyone calls `updateRSETHPrice()`. The price rises to reflect accrued rewards.
4. Attacker's rsETH is now worth more than deposited. Pre-existing holders' rsETH is worth proportionally less — their accrued yield has been captured by the attacker.

## Impact Explanation
This is **theft of unclaimed yield** (High impact per the allowed scope). Pre-existing rsETH holders lose a portion of their accrued staking yield on every deposit that occurs during a staleness window. The attacker captures the yield delta between the stale and true price. The magnitude scales with deposit size and staleness duration and is repeatable on every price-update cycle.

## Likelihood Explanation
**Medium.** No special privileges are required — any depositor can execute this. The attacker only needs to observe that rewards have accrued (e.g., by comparing `getTotalAssetDeposits()` against `rsETHPrice × totalSupply` on-chain) and deposit before `updateRSETHPrice()` is called. Since `updateRSETHPrice()` is called off-chain by bots or managers on a periodic schedule, a staleness window always exists. The `pricePercentageLimit` guard can further extend the window by blocking public price updates when accumulated rewards are large, requiring a manager call.

## Recommendation
Compute the rsETH price on-the-fly inside `getRsETHAmountToMint()` by invoking the equivalent of `_getTotalEthInProtocol()` directly rather than reading the stored `rsETHPrice`, or call `_updateRsETHPrice()` atomically at the start of each deposit transaction before computing the mint amount. Alternatively, enforce a price-freshness check that reverts deposits if `rsETHPrice` was last updated more than N blocks ago.

## Proof of Concept
```
Initial state:
  totalETH = 1000 ETH, totalRsETH = 1000, rsETHPrice = 1.000e18

Step 1: 10 ETH in staking rewards accrue inside EigenLayer/NodeDelegator balances.
  True price = 1010 / 1000 = 1.010e18
  Stored rsETHPrice = 1.000e18  (stale)

Step 2: Attacker calls depositETH{value: 100 ETH}(0, "").
  getRsETHAmountToMint = 100e18 * 1e18 / 1.000e18 = 100 rsETH minted
  (At true price, attacker should receive 100e18 / 1.010e18 ≈ 99.01 rsETH)
  New state: totalETH = 1110 ETH, totalRsETH = 1100

Step 3: updateRSETHPrice() is called.
  newRsETHPrice = 1110 / 1100 ≈ 1.009e18

Step 4: Attacker holds 100 rsETH worth 100 × 1.009 = 100.9 ETH.
  Attacker paid 100 ETH → profit ≈ 0.9 ETH extracted from existing holders' yield.

Foundry fork test plan:
  1. Fork mainnet at a block where rsETHPrice is stale (rewards accrued).
  2. Assert getTotalAssetDeposits() * assetPrice > rsETHPrice * totalSupply.
  3. Call depositETH with a large value; record rsETH minted.
  4. Call updateRSETHPrice().
  5. Assert attacker's rsETH value (rsETH * newRsETHPrice) > ETH deposited.
  6. Assert existing holders' per-rsETH ETH value decreased relative to pre-deposit state.
```