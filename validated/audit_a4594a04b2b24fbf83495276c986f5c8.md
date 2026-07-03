Audit Report

## Title
`LRTDepositPool::getRsETHAmountToMint()` Uses Stale Cached `rsETHPrice`, Allowing Depositors to Receive Excess rsETH — (File: `contracts/LRTDepositPool.sol`)

## Summary

`LRTOracle` stores `rsETHPrice` as a persistent state variable updated only when `updateRSETHPrice()` is explicitly called. `LRTDepositPool::getRsETHAmountToMint()` reads this cached value directly without triggering a refresh. Every deposit via `depositETH()` or `depositAsset()` computes the rsETH mint amount using this potentially stale price, causing depositors to receive more rsETH than their contribution warrants and diluting the accrued yield of existing holders.

## Finding Description

`LRTOracle` declares `rsETHPrice` as a mutable state variable:

```solidity
// contracts/LRTOracle.sol L28
uint256 public override rsETHPrice;
```

It is only updated when `updateRSETHPrice()` (or `updateRSETHPriceAsManager()`) is explicitly called — neither is invoked atomically during the deposit flow:

```solidity
// contracts/LRTOracle.sol L87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

`LRTDepositPool::getRsETHAmountToMint()` reads the cached value directly:

```solidity
// contracts/LRTDepositPool.sol L506-521
function getRsETHAmountToMint(address asset, uint256 amount)
    public view override returns (uint256 rsethAmountToMint)
{
    address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
    ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);
    rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
}
```

This function is called by `_beforeDeposit()`:

```solidity
// contracts/LRTDepositPool.sol L665
rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
```

Which is invoked by both `depositETH()` (L87) and `depositAsset()` (L111).

Since rsETH is yield-bearing, `rsETHPrice` increases monotonically between keeper updates. A stale (lower) `rsETHPrice` in the denominator of `(amount × assetPrice) / rsETHPrice` produces a larger `rsethAmountToMint` than the depositor is entitled to. The excess rsETH represents a claim on TVL not backed by the depositor's contribution — it is extracted from yield that had already accrued to existing holders.

An additional compounding factor exists: if the true price increase since the last update exceeds `pricePercentageLimit`, `updateRSETHPrice()` reverts with `PriceAboveDailyThreshold` for non-manager callers (L263), meaning the price can remain stale for an extended period until a manager intervenes, widening the exploitation window.

## Impact Explanation

**High — Theft of unclaimed yield.**

Every deposit made while `rsETHPrice` is stale mints excess rsETH. That excess represents a proportional claim on the protocol's TVL that was not contributed by the depositor. When `updateRSETHPrice()` is eventually called, the new price is computed as `totalETHInProtocol / rsethSupply`; the inflated `rsethSupply` from stale-price deposits causes the updated price to be lower than it would otherwise be, permanently transferring a portion of accrued yield from existing holders to the depositor. This matches the allowed impact class "Theft of unclaimed yield."

## Likelihood Explanation

**Medium.**

`updateRSETHPrice()` is public but is not called atomically within the deposit flow. The protocol relies on an off-chain keeper, so there is always a non-zero staleness window between updates. Any unprivileged depositor — including one who deliberately monitors the staleness window — can exploit this by simply calling `depositETH()` or `depositAsset()` when the price has not been recently refreshed. No front-running, admin compromise, or special privilege is required. The attack is repeatable across every keeper interval.

## Recommendation

Refresh `rsETHPrice` atomically before computing the mint amount. Because `updateRSETHPrice()` is state-mutating and `whenNotPaused`, `getRsETHAmountToMint()` must be changed from `view` to a regular function, and the `_beforeDeposit()` → `depositETH()`/`depositAsset()` call chain must be updated accordingly:

```diff
-function getRsETHAmountToMint(address asset, uint256 amount)
-    public view override returns (uint256 rsethAmountToMint)
+function getRsETHAmountToMint(address asset, uint256 amount)
+    public override returns (uint256 rsethAmountToMint)
 {
     address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
     ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);
+    lrtOracle.updateRSETHPrice();
     rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
 }
```

Alternatively, expose a pure computation path in `LRTOracle` that calculates the current price on-the-fly from `_getTotalEthInProtocol()` and `rsethSupply` without writing state, and use that value in `getRsETHAmountToMint()`.

## Proof of Concept

1. Observe that `rsETHPrice` in `LRTOracle` was last updated at time T. The true current price (if `updateRSETHPrice()` were called now) is higher due to accrued staking rewards.
2. Alice calls `depositETH{value: 10 ether}()` without calling `updateRSETHPrice()` first.
3. `_beforeDeposit()` → `getRsETHAmountToMint()` computes: `10e18 * 1e18 / stalePrice` (e.g., `1.001e18`), yielding ~9.990 rsETH.
4. The correct amount at the true price (e.g., `1.002e18`) would be ~9.980 rsETH.
5. Alice receives ~0.010 rsETH more than she is entitled to, extracted from the yield of existing holders.
6. When the keeper (or anyone) subsequently calls `updateRSETHPrice()`, the new price is computed over the inflated `rsethSupply`, permanently locking in the dilution.

**Foundry fork test plan:**
- Fork mainnet at a block where `rsETHPrice` is known.
- Advance time by one keeper interval without calling `updateRSETHPrice()`.
- Record `rsETHPrice` (stale) and compute the true price by calling `_getTotalEthInProtocol()` off-chain.
- Call `depositETH{value: X}()` as an unprivileged address.
- Assert that the minted rsETH exceeds `X * 1e18 / truePrice`.
- Call `updateRSETHPrice()` and assert the new price is lower than it would have been without the stale-price deposit, confirming yield dilution.