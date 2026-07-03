Audit Report

## Title
Stale `rsETHPrice` Used in Deposit Minting Calculation Without Prior Refresh - (File: contracts/LRTDepositPool.sol)

## Summary

`LRTDepositPool.depositETH()` and `depositAsset()` compute the rsETH mint amount by dividing deposited asset value by `lrtOracle.rsETHPrice()`, a storage variable that is only written when `updateRSETHPrice()` is explicitly called. Neither deposit path refreshes this value before use. When restaking yield has accrued since the last price update, the stored price is lower than the true rate, causing depositors to receive excess rsETH at the expense of existing holders' accrued yield.

## Finding Description

`getRsETHAmountToMint()` at `LRTDepositPool.sol:520` computes:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`rsETHPrice` is a plain storage variable (`LRTOracle.sol:28`) written only inside `_updateRsETHPrice()`, which is invoked by the public `updateRSETHPrice()` (`LRTOracle.sol:87-89`) or the manager-only `updateRSETHPriceAsManager()`. The deposit entry points `depositETH()` (`LRTDepositPool.sol:76-93`) and `depositAsset()` (`LRTDepositPool.sol:99-118`) both delegate to `_beforeDeposit()` (`LRTDepositPool.sol:648-670`), which is declared `private view` and therefore cannot invoke any state-changing price refresh before calling `getRsETHAmountToMint()`.

As restaking yield accrues, `_getTotalEthInProtocol()` grows while `rsETHPrice` remains frozen at its last-written value. Because the mint formula divides by `rsETHPrice`, a stale (understated) denominator produces a larger rsETH output than the depositor's proportional share of TVL warrants. After the deposit, when `updateRSETHPrice()` is eventually called, the new price is computed as `(totalETH - fee) / rsETHSupply` where `rsETHSupply` now includes the excess tokens, permanently diluting all pre-existing holders.

The `minRSETHAmountExpected` slippage guard protects the depositor from receiving *less* than expected; it provides no protection against the depositor receiving *more* than their fair share.

## Impact Explanation

Every rsETH token represents a proportional claim on the protocol's total ETH. When a depositor mints rsETH at a stale (understated) price, they receive a larger fraction than their deposit justifies. The excess fraction is taken from existing holders: the same total ETH is now split among more rsETH tokens, so each pre-existing token is worth less ETH. The loss equals the excess rsETH minted multiplied by the true rsETH price — a direct, quantifiable transfer of accrued yield from existing holders to the depositor.

**Impact class: High — Theft of unclaimed yield.**

## Likelihood Explanation

`updateRSETHPrice()` is driven by an off-chain keeper on a periodic schedule and is not called atomically with deposits. Any unprivileged address can call `depositETH()` or `depositAsset()`. An attacker who observes that `rsETHPrice` has not been refreshed recently (trivially detectable on-chain by comparing `rsETHPrice` against a freshly computed TVL/supply ratio) can deposit immediately before the next keeper update. No special privilege, flash loan, or governance access is required. The window is predictable and the attack is repeatable on every keeper cycle.

## Recommendation

Refactor `_beforeDeposit()` from `private view` to `private` and invoke `updateRSETHPrice()` (or the internal `_updateRsETHPrice()` directly, if `LRTOracle` exposes it to `LRTDepositPool`) at the start of the function, before `getRsETHAmountToMint()` is called. This ensures every deposit uses the freshest possible exchange rate. Alternatively, record a `lastPriceUpdateTimestamp` in `LRTOracle` and revert in `getRsETHAmountToMint()` if the price is older than an acceptable staleness window (e.g., 1 hour).

## Proof of Concept

**Setup:**
- Protocol TVL: 1 000 ETH; rsETH supply: 1 000 rsETH.
- Yield accrues: true price = 1.01 ETH/rsETH; stored `rsETHPrice` = 1.00 ETH/rsETH (keeper has not yet called `updateRSETHPrice()`).

**Attack sequence:**
1. Attacker calls `depositETH{value: 10 ether}(0, "")`.
2. `_beforeDeposit` → `getRsETHAmountToMint(ETH, 10e18)` computes `10e18 * 1e18 / 1e18 = 10 rsETH` (stale price 1.00).
3. Fair mint at true price 1.01: `10 / 1.01 ≈ 9.901 rsETH`.
4. Attacker receives **10 rsETH** — an excess of **~0.099 rsETH** (~0.10 ETH at true price).
5. Existing 1 000 rsETH holders collectively lose that ~0.10 ETH of accrued yield.
6. Attacker (or keeper) calls `updateRSETHPrice()`; new price is computed over the now-diluted supply, locking in the loss.
7. Attack repeats on every subsequent accrual cycle.

**Foundry fork test plan:**
```solidity
function testStaleRsETHPriceDilution() public {
    // 1. Fork mainnet, warp forward to let yield accrue without calling updateRSETHPrice()
    // 2. Record rsETHPrice (stale) and compute true price via getTotalAssetDeposits / rsETH.totalSupply
    // 3. Assert stalePrice < truePrice
    // 4. Record victim's rsETH balance and totalSupply
    // 5. Attacker depositETH(10 ether)
    // 6. Call updateRSETHPrice()
    // 7. Assert victim's ETH-equivalent balance decreased (rsETH price post-deposit < pre-deposit true price)
    // 8. Assert attacker rsETH balance > 10e18 * 1e18 / truePrice
}
```