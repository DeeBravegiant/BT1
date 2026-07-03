Audit Report

## Title
Stale Cached `rsETHPrice` Used in Deposit Mint Calculation Enables Yield Theft from Existing rsETH Holders - (File: contracts/LRTDepositPool.sol)

## Summary
`LRTOracle` stores `rsETHPrice` as a state variable updated only when `updateRSETHPrice()` is explicitly called. `LRTDepositPool.getRsETHAmountToMint` reads this cached value as the denominator when computing rsETH to mint, while the numerator uses a live Chainlink price. Because rsETH is yield-bearing and its true price continuously rises between updates, a depositor can exploit the staleness gap to receive more rsETH than fair value, extracting yield that belongs to existing holders.

## Finding Description
`LRTOracle.rsETHPrice` is a plain storage variable written only inside `_updateRsETHPrice()`:

```solidity
// LRTOracle.sol:28
uint256 public override rsETHPrice;

// LRTOracle.sol:313
rsETHPrice = newRsETHPrice;
```

`updateRSETHPrice()` is publicly callable but is never invoked atomically within the deposit flow:

```solidity
// LRTOracle.sol:87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

`LRTDepositPool.getRsETHAmountToMint` computes the mint amount using a live asset price in the numerator and the stale cached `rsETHPrice` in the denominator:

```solidity
// LRTDepositPool.sol:519-520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`depositAsset` calls `_beforeDeposit` → `getRsETHAmountToMint` with no price refresh anywhere in the call chain. As staking rewards accrue between updates, `totalETHInProtocol` grows but `rsETHPrice` stays fixed at its last-written value. The denominator is therefore always ≤ the true current price, causing the division to produce a larger-than-correct rsETH amount. After depositing, the attacker calls the public `updateRSETHPrice()`, which recomputes the price from live TVL and supply and writes the corrected (higher) value to storage. The attacker's over-minted rsETH is now worth more than the deposited assets; the surplus is extracted from the yield that had accrued to pre-existing holders.

No existing guard prevents this: `minRSETHAmountExpected` is a lower-bound slippage check that protects the depositor from receiving *less* than expected, not from receiving *more* than fair value. The `pricePercentageLimit` check inside `_updateRsETHPrice` only blocks a public price update when the increase exceeds the configured threshold, which for normal yield accrual (hours-scale staleness, ~4–5% APY) is well within any reasonable limit.

## Impact Explanation
Every deposit made while `rsETHPrice` is stale mints excess rsETH. When the price is subsequently updated, the attacker's position is worth more than the deposited value. The surplus is sourced directly from the unclaimed yield that had accrued to existing rsETH holders but was not yet reflected in the cached price. This matches the allowed impact: **High — Theft of unclaimed yield**.

## Likelihood Explanation
`updateRSETHPrice()` is not called on every deposit; it is driven by off-chain keepers or manual calls. Any gap between consecutive updates (routinely hours in low-activity periods) creates an exploitable window. The attack requires no privileged role, no governance action, and no external protocol compromise — only two sequential public calls (`depositAsset` then `updateRSETHPrice`). It is repeatable on every staleness window and scales linearly with deposit size.

## Recommendation
Call `_updateRsETHPrice()` (or compute the live price inline) before evaluating `getRsETHAmountToMint` inside the deposit flow, so the denominator always reflects the current TVL/supply ratio at the moment of deposit. Alternatively, derive the mint amount directly from a freshly computed price rather than reading the `rsETHPrice` storage variable.

## Proof of Concept
1. Assume `rsETHPrice` was last updated 6 hours ago at `1.040e18`. Staking rewards have since accrued; true price is `1.041e18` (TVL has grown by ~0.096%).
2. Attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, 0, "")`.
3. `getRsETHAmountToMint` executes: `(1000e18 * 1e18) / 1.040e18 = 961.538... rsETH` (stale denominator).
4. Fair amount at true price: `(1000e18 * 1e18) / 1.041e18 = 960.615... rsETH`.
5. Attacker receives `≈0.923 rsETH` in excess of fair value.
6. Attacker calls `updateRSETHPrice()`; price corrects upward toward `1.041e18` (diluted slightly by the over-minted supply, but still above `1.040e18`).
7. Attacker's rsETH position is worth more than the `1000 ETH` deposited; the delta is extracted from the yield that pre-existing holders had accrued but not yet realized.

**Foundry fork test outline:**
```solidity
// Fork mainnet at block B (rsETHPrice stale by N hours)
// Record attacker rsETH balance after depositAsset
// Call updateRSETHPrice()
// Assert attacker ETH-equivalent value > deposited amount
// Assert existing holder ETH-equivalent value < pre-deposit value
```