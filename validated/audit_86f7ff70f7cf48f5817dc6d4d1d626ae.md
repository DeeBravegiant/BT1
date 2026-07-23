The code confirms all claims. Let me verify one more detail about the surplus distribution path when spread fees are zero.

Audit Report

## Title
`notionalFeeToken0Scaled`/`notionalFeeToken1Scaled` Unconditionally Cleared to Zero When Floor-Division Yields No Transferable Tokens, Permanently Destroying Accumulated Notional Fees - (File: `metric-core/contracts/MetricOmmPool.sol`)

## Summary
In `MetricOmmPool.collectFees`, the notional fee accumulators `notionalFeeToken0Scaled` and `notionalFeeToken1Scaled` are unconditionally reset to zero at lines 429–430 regardless of whether the floor-division conversion to external token units produced any transferable amount. When accumulated scaled fees are below `TOKEN_X_SCALE_MULTIPLIER` (e.g., `10^12` for a 6-decimal token), no transfer occurs but the accumulator is still zeroed, permanently destroying the tracked fee revenue. In pools configured with zero spread fees, the orphaned balance can never be recovered through any subsequent `collectFees` call.

## Finding Description
`collectFees` (lines 365–434) reads `notionalFeeToken0Scaled` and `notionalFeeToken1Scaled` into locals, computes per-recipient splits, then converts to external units via `deltasScaledToExternal` with `Math.Rounding.Floor` (lines 411–414):

```solidity
// lines 626-627
deltaAmount0 = scaledDeltaAmount0 / TOKEN_0_SCALE_MULTIPLIER;
```

For a 6-decimal token0, `TOKEN_0_SCALE_MULTIPLIER = 10^12`. Any `notionalFeeToken0Scaled < 10^12` produces `deltaAmount0 = 0`. The conditional transfers at lines 416–427 are skipped, but lines 429–430 execute unconditionally:

```solidity
notionalFeeToken0Scaled = 0;   // line 429
notionalFeeToken1Scaled = 0;   // line 430
```

The orphaned scaled units remain in the pool's ERC-20 balance but are no longer tracked by either `binTotals.scaledToken0` or `notionalFeeToken0Scaled`. On the next `collectFees` call, the surplus formula (lines 385–388) includes these orphaned units in `surplus0Scaled`. However, when `spreadSumE6 == 0` (lines 391–395), all spread fee allocations are forced to zero:

```solidity
uint256 spreadFee0ToAdminScaled    = spreadSumE6 == 0 ? 0 : ...;
uint256 spreadFee0ToProtocolScaled = spreadSumE6 == 0 ? 0 : ...;
```

The early-return guard at lines 377–379 only fires when *both* spread and notional fees are zero, so a notional-only pool (`spreadSumE6 = 0`, `notionalSumE8 > 0`) proceeds through the full function. In that configuration the orphaned surplus is never distributed and the tokens are permanently stuck.

`collectPoolFees` on the factory (line 379) carries no access control beyond `nonReentrant`, making it callable by any address.

## Impact Explanation
Direct, permanent loss of protocol and admin fee revenue. Accumulated notional fees paid by traders are destroyed without any corresponding transfer. In a notional-only fee pool (`protocolSpreadFeeE6 = 0`, `adminSpreadFeeE6 = 0`), the orphaned tokens can never be recovered through any on-chain path. The maximum loss per invocation is `TOKEN_X_SCALE_MULTIPLIER − 1` scaled units per token per recipient leg. An adversary can call `collectPoolFees` repeatedly after small swaps to drain the accumulator in dust increments, causing unbounded cumulative loss of protocol/admin fee revenue. This is a direct loss of owed protocol fees above Sherlock thresholds when repeated.

## Likelihood Explanation
`collectPoolFees` is callable by any address with no restriction. Pools pairing 18-decimal tokens with low-decimal tokens (USDC at 6 decimals, USDT at 6 decimals) are the primary deployment target and produce `TOKEN_0_SCALE_MULTIPLIER = 10^12`. Small swaps generating sub-unit notional fees are routine in any active pool. No privileged access, special setup, or non-standard token behavior is required. The attack is fully repeatable.

## Recommendation
Subtract from the accumulators only the scaled equivalent of the external amount actually transferred, rather than unconditionally zeroing them:

```solidity
// Replace lines 429-430 with:
uint256 paid0Scaled = (totalFee0ToAdmin + totalFee0ToProtocol) * TOKEN_0_SCALE_MULTIPLIER;
uint256 paid1Scaled = (totalFee1ToAdmin + totalFee1ToProtocol) * TOKEN_1_SCALE_MULTIPLIER;
notionalFeeToken0Scaled = uint128(notionalFee0AmountScaled > paid0Scaled
    ? notionalFee0AmountScaled - paid0Scaled : 0);
notionalFeeToken1Scaled = uint128(notionalFee1AmountScaled > paid1Scaled
    ? notionalFee1AmountScaled - paid1Scaled : 0);
```

Alternatively, gate the entire accumulator reset on a minimum threshold check before clearing.

## Proof of Concept
1. Deploy a pool with USDC (6 decimals) as token0 and an 18-decimal token as token1. Configure `protocolNotionalFeeE8 = 1_000_000` (1%), `protocolSpreadFeeE6 = 0`, `adminSpreadFeeE6 = 0`.
2. `TOKEN_0_SCALE_MULTIPLIER = 10^12`.
3. Execute a swap that produces `notionalFeeToken0Scaled = 5 * 10^11` (< `10^12`), corresponding to ~0.5 USDC notional fee — a realistic small swap.
4. Any address calls `factory.collectPoolFees(pool)` (no access control at line 379).
5. Inside `collectFees`: `totalFee0ToProtocol = (5 * 10^11) / 10^12 = 0` (floor division, line 626). No transfer occurs.
6. `notionalFeeToken0Scaled` is set to 0 (line 429). The 5 * 10^11 scaled units remain in the pool's ERC-20 balance but are now untracked.
7. On the next `collectFees` call, `surplus0Scaled` includes the orphaned units, but since `spreadSumE6 = 0`, `spreadFee0ToProtocolScaled = 0` and `spreadFee0ToAdminScaled = 0` (lines 391, 394). The tokens are never distributed.
8. Repeat steps 3–7 to accumulate unbounded losses of notional fee revenue.