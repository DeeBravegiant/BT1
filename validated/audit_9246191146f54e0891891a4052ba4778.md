Audit Report

## Title
Stale `rsETHPrice` in Deposit Mint Calculation Allows Depositors to Capture Accrued Yield - (File: `contracts/LRTDepositPool.sol`)

## Summary
`LRTDepositPool.getRsETHAmountToMint()` divides by `lrtOracle.rsETHPrice()`, a stored state variable in `LRTOracle` that is never refreshed atomically within the deposit flow. As EigenLayer staking rewards accrue and increase the true protocol TVL, the stored price lags behind the true rate. Any depositor who deposits during this staleness window receives more rsETH than their deposit warrants, diluting the yield that belongs to existing rsETH holders.

## Finding Description
`LRTOracle.rsETHPrice` is a plain storage variable written only inside `_updateRsETHPrice()`, which is triggered by the public `updateRSETHPrice()` or the manager-only `updateRSETHPriceAsManager()`. Neither is called anywhere in the deposit flow. A `grep` across the repository confirms `updateRSETHPrice` appears exclusively in `LRTOracle.sol`.

The mint calculation in `LRTDepositPool`:

```solidity
// contracts/LRTDepositPool.sol:520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

reads the last-written value of:

```solidity
// contracts/LRTOracle.sol:28
uint256 public override rsETHPrice;
```

which is only updated at:

```solidity
// contracts/LRTOracle.sol:313
rsETHPrice = newRsETHPrice;
```

Meanwhile, `_getTotalEthInProtocol()` (called inside `_updateRsETHPrice()`) reads live EigenLayer strategy balances and EigenPod shares, so the true TVL grows continuously as consensus-layer and restaking rewards accrue. The stored `rsETHPrice` does not reflect this growth until an explicit update call.

Because `rsethAmountToMint = (amount × assetPrice) / rsETHPrice`, a denominator that is lower than the true current rate causes the depositor to receive excess rsETH. When `updateRSETHPrice()` is eventually called, the new price is computed over a larger supply, so the price increase is smaller than it would have been — existing holders receive proportionally less yield than they earned.

The staleness window is widened further by the `pricePercentageLimit` guard at lines 260–265 of `LRTOracle.sol`: if the accumulated price increase exceeds the configured threshold, a public call to `updateRSETHPrice()` reverts with `PriceAboveDailyThreshold`, leaving only the manager able to update the price and extending the window during which the stale (lower) price is exploitable.

## Impact Explanation
**High — Theft of unclaimed yield.** Existing rsETH holders accumulate yield as EigenLayer rewards increase the protocol TVL. This yield is reflected in a rising rsETH/ETH rate. When a depositor mints rsETH against a stale (lower) rate, they receive a larger share of the total supply than their deposit warrants. When `updateRSETHPrice()` is eventually called, the price increase is smaller than it would have been because the supply is now larger, meaning existing holders receive less yield than they earned. The magnitude scales with deposit size and the duration of the staleness window, and the attack is repeatable on every staleness window.

## Likelihood Explanation
**Medium.** The attack requires no special privileges — any caller of `depositETH` or `depositAsset` can exploit it. There is always a non-zero staleness window between the last price update and any given deposit. A sophisticated depositor can monitor on-chain TVL inputs (EigenPod shares, strategy balances) to identify when the stored price is most stale and time a large deposit accordingly. The `pricePercentageLimit` guard can extend the window by blocking public price updates, making the attack more reliable in high-reward periods.

## Recommendation
Call `lrtOracle.updateRSETHPrice()` at the beginning of `getRsETHAmountToMint()` (or inside `_beforeDeposit()`) before reading `lrtOracle.rsETHPrice()`. This ensures the mint calculation always uses the current exchange rate. Note that `updateRSETHPrice()` is `whenNotPaused`, so the call should be guarded or the price-refresh logic inlined to avoid introducing a new revert path. Alternatively, compute the mint amount directly from the live TVL and supply rather than from the stored price variable.

## Proof of Concept
1. EigenLayer staking rewards accrue over several hours, increasing the true protocol TVL. The true rsETH/ETH rate is now `1.002e18`, but `lrtOracle.rsETHPrice` still stores `1.001e18` from the last update.
2. Attacker calls `LRTDepositPool.depositETH{value: 1000 ether}(0, "")`.
3. `_beforeDeposit` → `getRsETHAmountToMint(ETH, 1000e18)` computes:
   - Stale: `(1000e18 × 1e18) / 1.001e18 ≈ 999.001 rsETH`
   - True rate: `(1000e18 × 1e18) / 1.002e18 ≈ 998.004 rsETH`
   - Attacker receives ≈ 0.997 excess rsETH.
4. When `updateRSETHPrice()` is called, the new price is computed over the inflated supply, so the price increase is smaller — existing holders receive less yield than they earned.
5. The attacker repeats this on every staleness window with no special privileges.

**Foundry fork test plan:** Fork mainnet, set `rsETHPrice` to a value slightly below the live computed price (obtained by calling `_getTotalEthInProtocol()` equivalent off-chain), call `depositETH` as an unprivileged address, then call `updateRSETHPrice()` and compare the resulting price to what it would have been without the intervening deposit. Assert that existing holders' ETH-denominated balance is lower than it would have been at the true pre-deposit rate.