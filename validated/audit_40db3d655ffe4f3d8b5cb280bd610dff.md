Audit Report

## Title
Pool Admin Bypasses `maxAdminSpreadFeeE6` Cap via Uncapped Per-Bin Additional Fees — (`metric-core/contracts/MetricOmmPoolFactory.sol`)

## Summary

`setPoolBinAdditionalFees` allows a pool admin to set `addFeeBuyE6`/`addFeeSellE6` on any bin to any `uint16` value (0–65535, ~6.55% in E6 units) with no cap validation and no timelock. These per-bin fees are additive to the base fee in every swap through the affected bin, meaning the pool admin can effectively exceed the `maxAdminSpreadFeeE6` cap enforced on `setPoolAdminFees`. The README explicitly states this class of finding is valid: pool admin "Cannot exceed caps or bypass timelocks."

## Finding Description

`setPoolAdminFees` enforces the factory-level cap at `MetricOmmPoolFactory.sol` L414–415:

```solidity
if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
```

`setPoolBinAdditionalFees` at L450–457 performs no such check — it passes the caller-supplied values directly to `setBinAdditionalFees` on the pool. The pool-level handler at `MetricOmmPool.sol` L464–474 also performs no cap check; it only validates the bin index range. The per-bin fees are then injected additively into every swap at L1177:

```solidity
params.baseFeeX64 + Math.mulDiv(binState.addFeeSellE6, ONE_X64, 1e6)
```

`HARD_MAX_SPREAD_FEE_E6 = 200_000` (20%) is the ceiling for the owner-configurable `maxAdminSpreadFeeE6`. A pool admin is bounded by whatever `maxAdminSpreadFeeE6` is set to (which can be as low as 0). However, via `setPoolBinAdditionalFees`, the same admin can add up to `uint16.max = 65535` (~6.55%) on top of the base fee per bin, with no cap check and immediate effect. This directly bypasses the cap system.

The exploit path:
1. Admin observes a pending swap targeting the active bin in the mempool.
2. Admin calls `setPoolBinAdditionalFees(pool, activeBin, 0, 65535)` with higher gas priority.
3. Victim's swap executes with `currBinSellFeeX64` inflated by ~6.55%, causing the callback to transfer more token0 than the pre-swap quote indicated.
4. Admin resets the fee to 0 in the same block.

## Impact Explanation

This is an admin-boundary break: the pool admin exceeds the fee cap enforced by `maxAdminSpreadFeeE6` via an uncapped parallel path. The swapper suffers a direct, quantifiable loss of input tokens relative to the price they observed. The excess fee accrues as LP fee inside the bin. This constitutes a swap conservation failure — the trader pays more than the bin curve permits at the quoted spread. Severity: **Medium**, consistent with the README's explicit validity criterion for pool admin cap bypasses.

## Likelihood Explanation

Any pool whose admin is an EOA or a compromised multisig can trigger this. The attack is atomic (single block), requires no special setup beyond mempool visibility (relevant on Ethereum, one of the three deployment chains), and leaves no persistent trace beyond the fee reset. The `maxAdminSpreadFeeE6` cap can be set to any value by the factory owner, including values well below 65535, making the bypass magnitude variable but always possible up to ~6.55%.

## Recommendation

1. **Cap**: Add a cap check inside `setPoolBinAdditionalFees` mirroring `setPoolAdminFees`:
   ```solidity
   if (addFeeBuyE6 > maxAdminSpreadFeeE6 || addFeeSellE6 > maxAdminSpreadFeeE6)
     revert AdminFeeTooHigh();
   ```
   Alternatively, introduce a dedicated `maxAdminBinAdditionalFeeE6` cap settable by the factory owner.
2. **Timelock**: Introduce a propose/execute pattern for per-bin fee changes analogous to `proposePoolPriceProvider`/`executePoolPriceProviderUpdate`, giving users advance notice before a fee spike takes effect.

## Proof of Concept

```solidity
// Setup: maxAdminSpreadFeeE6 = 5000 (0.5%), so setPoolAdminFees is capped at 0.5%
// Attacker = pool admin

// 1. Observe victim's swap tx in mempool (sell token0 into active bin)
// 2. Front-run with fee spike — no cap check, succeeds even though 65535 >> maxAdminSpreadFeeE6
factory.setPoolBinAdditionalFees(pool, activeBin, 0, 65535); // addFeeSellE6 = ~6.55%

// 3. Victim's swap executes: baseFeeX64 + mulDiv(65535, ONE_X64, 1e6) applied
//    amountCalculatedScaled (token0 input) is inflated by ~6.55% vs quoted rate
//    swap(SwapParams{zeroForOne: true, ...})

// 4. Admin resets fee in same block — no trace
factory.setPoolBinAdditionalFees(pool, activeBin, 0, 0);

// Foundry test: assert token0 transferred by victim > pre-spike quote
// Verify: setPoolAdminFees(pool, 65535, 0) reverts AdminFeeTooHigh
//         setPoolBinAdditionalFees(pool, 0, 0, 65535) succeeds — demonstrating bypass
```