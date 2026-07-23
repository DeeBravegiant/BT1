All five key facts are confirmed in the production code:

1. `collectPoolFees` has no access control — `external override nonReentrant` only. [1](#0-0) 

2. `notionalFeeToken0Scaled` and `notionalFeeToken1Scaled` are unconditionally zeroed at lines 429–430 regardless of whether any transfer occurred. [2](#0-1) 

3. Fee-out conversion uses integer division (floor). [3](#0-2) 

4. `TOKEN_0_SCALE_MULTIPLIER` is `10^(max(18,decimals) - decimals)` — for USDC (6 dec) = `10^12`. [4](#0-3) 

5. When `spreadSumE6 == 0`, the surplus (which absorbs the cleared notional) is multiplied by zero and never distributed. [5](#0-4) 

---

Audit Report

## Title
Permissionless `collectPoolFees` unconditionally clears notional fee accumulator even when floor-rounding produces zero external transfer, permanently freezing protocol fees — (`metric-core/contracts/MetricOmmPool.sol`)

## Summary

`MetricOmmPool.collectFees()` always resets `notionalFeeToken0Scaled` and `notionalFeeToken1Scaled` to zero after fee collection, even when the accumulated scaled amount is below `TOKEN_X_SCALE_MULTIPLIER` and the floor-rounded external transfer amount is zero. Because `MetricOmmPoolFactory.collectPoolFees()` is permissionless, an attacker can call it after every small swap to prevent the notional accumulator from ever reaching the transfer threshold, permanently freezing protocol and admin notional fees inside the pool with no recovery path when spread fee is zero.

## Finding Description

In `MetricOmmPool.collectFees()`, the notional fee accumulators are read, used to compute scaled fee splits, converted to external units via floor division, conditionally transferred, and then unconditionally zeroed:

```solidity
// L382-430
uint256 notionalFee0AmountScaled = notionalFeeToken0Scaled;
...
(uint256 totalFee0ToProtocol, uint256 totalFee1ToProtocol) =
    deltasScaledToExternal(totalFee0ToProtocolScaled, totalFee1ToProtocolScaled, Math.Rounding.Floor);
...
if (totalFee0ToProtocol > 0) { transferToken0(FACTORY, totalFee0ToProtocol); }
...
notionalFeeToken0Scaled = 0;   // ← always cleared
notionalFeeToken1Scaled = 0;
```

The floor conversion at L626–627 means any `notionalFeeToken0Scaled` value in `[1, TOKEN_0_SCALE_MULTIPLIER - 1]` produces `totalFee0ToProtocol = 0` with no transfer. For USDC (6 decimals), `TOKEN_0_SCALE_MULTIPLIER = 10^12`, so any accumulator value below `10^12` (~1 USDC) rounds to zero.

After the clear, on the next `collectFees` call the surplus recalculation at L385–388 absorbs the previously cleared amount:

```solidity
surplus0Scaled = balance0() * TOKEN_0_SCALE_MULTIPLIER
               - uint256(binTotals.scaledToken0)
               - notionalFee0AmountScaled;  // == 0 now
```

The cleared notional is now inside `surplus0Scaled`. When `spreadSumE6 == 0`, lines 391–395 set all spread fee shares to zero, so the surplus is never distributed — it is permanently frozen in the pool, unreachable by protocol, admin, or LPs.

The factory entry point has no access control:

```solidity
// MetricOmmPoolFactory.sol L379
function collectPoolFees(address pool) external override nonReentrant {
```

An attacker calls this after every swap that generates `notionalFeeXScaled < TOKEN_X_SCALE_MULTIPLIER`, clearing up to `TOKEN_X_SCALE_MULTIPLIER - 1` scaled units per call with zero transfer.

## Impact Explanation

Direct, permanent loss of protocol and admin notional fees. For a USDC/USDT pool with notional fee enabled and spread fee = 0, each attacker call can freeze up to ~0.999999 USDC in notional fees. The loss is non-recoverable: the cleared amount is not in `binTotals` (LPs cannot withdraw it), not in the notional accumulator (cleared), and the spread fee path is skipped. Over $1M in pool volume at 1% notional fee, an attacker spending ~$1,000 in L2 gas can freeze ~$10,000 in protocol fees. This satisfies the Medium threshold: loss > 0.01% and > $10 USD, replayable indefinitely.

## Likelihood Explanation

- **Trigger**: Permissionless `collectPoolFees` — no special role required.
- **Preconditions**: Low-decimal token pair (USDC/USDT, in-scope per README), `protocolNotionalFeeE8 > 0`, `protocolSpreadFeeE6 = 0` and `adminSpreadFeeE6 = 0` (a valid and documented configuration).
- **Cost**: L2 gas per call is $0.01–$0.10, well below the per-call fee denied (~$1 USDC).
- **Repeatability**: Indefinitely replayable after every swap below the threshold.

## Recommendation

Do not unconditionally reset the notional fee accumulators. Only clear the portion that was actually paid out in external units, carrying the remainder forward:

```solidity
uint256 paidOut0Scaled = (totalFee0ToAdmin + totalFee0ToProtocol) * TOKEN_0_SCALE_MULTIPLIER;
notionalFeeToken0Scaled = uint128(
    notionalFee0AmountScaled > paidOut0Scaled ? notionalFee0AmountScaled - paidOut0Scaled : 0
);
```

Alternatively, restrict `collectPoolFees` to the pool admin or protocol owner to eliminate the griefing vector.

## Proof of Concept

1. Deploy a USDC (6-decimal) / WETH pool with `protocolNotionalFeeE8 = 1_000_000` (1%), `protocolSpreadFeeE6 = 0`, `adminSpreadFeeE6 = 0`.
2. LP adds liquidity.
3. Trader swaps exactly 99 USDC (exact-in, zeroForOne). Notional fee on output: `~0.99 USDC` → `notionalFeeToken1Scaled ≈ 9.9 × 10^11 < 10^12`.
4. Attacker calls `MetricOmmPoolFactory.collectPoolFees(pool)`.
   - `totalFee1ToProtocol = 9.9 × 10^11 / 10^12 = 0` (floor division).
   - No transfer. `notionalFeeToken1Scaled = 0`.
5. ~0.99 USDC notional fee is permanently frozen in the pool.
6. Repeat after every ~99 USDC of swap volume. Over $1M in volume, ~$10,000 in notional fees are frozen.

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L379-379)
```text
  function collectPoolFees(address pool) external override nonReentrant {
```

**File:** metric-core/contracts/MetricOmmPool.sol (L48-51)
```text
  /// @notice Multiplier to scale token0 external amounts to internal: 10^(max(18, decimals) - token0.decimals())
  uint256 internal immutable TOKEN_0_SCALE_MULTIPLIER;
  /// @notice Multiplier to scale token1 external amounts to internal: 10^(max(18, decimals) - token1.decimals())
  uint256 internal immutable TOKEN_1_SCALE_MULTIPLIER;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L391-395)
```text
      uint256 spreadFee0ToAdminScaled = spreadSumE6 == 0 ? 0 : (surplus0Scaled * adminSpreadFeeE6_) / spreadSumE6;
      uint256 spreadFee1ToAdminScaled = spreadSumE6 == 0 ? 0 : (surplus1Scaled * adminSpreadFeeE6_) / spreadSumE6;

      uint256 spreadFee0ToProtocolScaled = spreadSumE6 == 0 ? 0 : (surplus0Scaled * protocolSpreadFeeE6_) / spreadSumE6;
      uint256 spreadFee1ToProtocolScaled = spreadSumE6 == 0 ? 0 : (surplus1Scaled * protocolSpreadFeeE6_) / spreadSumE6;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L429-430)
```text
      notionalFeeToken0Scaled = 0;
      notionalFeeToken1Scaled = 0;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L626-627)
```text
      deltaAmount0 = scaledDeltaAmount0 / TOKEN_0_SCALE_MULTIPLIER;
      deltaAmount1 = scaledDeltaAmount1 / TOKEN_1_SCALE_MULTIPLIER;
```
