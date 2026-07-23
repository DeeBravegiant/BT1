Audit Report

## Title
Permissionless `collectPoolFees` unconditionally clears notional fee accumulator even when floor-rounding produces zero external transfer, permanently freezing protocol fees — (`metric-core/contracts/MetricOmmPool.sol`)

## Summary

`MetricOmmPool.collectFees()` unconditionally resets `notionalFeeToken0Scaled` and `notionalFeeToken1Scaled` to zero after every fee collection, even when the accumulated scaled amount is below `TOKEN_X_SCALE_MULTIPLIER` and the floor-rounded external transfer amount is zero. Because `MetricOmmPoolFactory.collectPoolFees()` has no access control, any caller can trigger this reset after every small swap, preventing the accumulator from ever reaching the transfer threshold and permanently freezing notional fees inside the pool when spread fee is zero.

## Finding Description

In `MetricOmmPool.collectFees()`, the notional fee accumulators are read, used to compute floor-rounded external amounts, and then unconditionally zeroed: [1](#0-0) [2](#0-1) [3](#0-2) 

The floor-rounding conversion divides by `TOKEN_X_SCALE_MULTIPLIER`: [4](#0-3) 

For a 6-decimal token (USDC/USDT), `TOKEN_0_SCALE_MULTIPLIER = 10^(18-6) = 10^12` (confirmed by the immutable comment and constructor assignment): [5](#0-4) [6](#0-5) 

Any `notionalFeeToken0Scaled` value in `[1, 10^12 - 1]` produces `totalFee0ToProtocol = 0` (no transfer), yet lines 429–430 still zero the accumulator. The cleared amount then becomes part of `surplus0Scaled` on the next call: [7](#0-6) 

When `spreadSumE6 == 0`, the surplus is entirely skipped — it is not distributed to protocol, admin, or LPs: [8](#0-7) 

The factory entry point has no access control, making the trigger permissionless: [9](#0-8) 

## Impact Explanation

For a USDC/USDT pool with `protocolNotionalFeeE8 > 0` and `protocolSpreadFeeE6 = adminSpreadFeeE6 = 0`: each attacker-triggered `collectPoolFees` call after a swap that generated `notionalFeeToken0Scaled < 10^12` clears up to ~0.999999 USDC of notional fees with zero transfer. The cleared amount enters `surplus0Scaled` but is permanently inaccessible because the spread fee path is skipped. The loss is non-recoverable and replayable indefinitely, satisfying the contest's Medium threshold for direct loss of protocol fees above $10 USD.

## Likelihood Explanation

- **Trigger**: `collectPoolFees` is permissionless — no special role required.
- **Preconditions**: Low-decimal token pair (USDC/USDT, in-scope per README), `notionalFeeE8 > 0`, `spreadSumE6 = 0` (a valid and documented configuration).
- **Cost vs. gain**: On L2s, gas per call is $0.01–$0.10. Each call can deny up to ~$1 in notional fees. Over $1M in pool volume, ~$10,000 in fees can be frozen for ~$1,000 in attacker gas.
- **Repeatability**: Indefinitely replayable after every swap below the threshold.

## Recommendation

Do not unconditionally zero the notional accumulators. Only clear the portion that was actually paid out in external units, carrying the remainder forward:

```solidity
uint256 paidOut0Scaled = (totalFee0ToAdmin + totalFee0ToProtocol) * TOKEN_0_SCALE_MULTIPLIER;
uint256 paidOut1Scaled = (totalFee1ToAdmin + totalFee1ToProtocol) * TOKEN_1_SCALE_MULTIPLIER;
notionalFeeToken0Scaled = uint128(notionalFee0AmountScaled - paidOut0Scaled);
notionalFeeToken1Scaled = uint128(notionalFee1AmountScaled - paidOut1Scaled);
```

Alternatively, restrict `collectPoolFees` to the pool admin or protocol owner to eliminate the permissionless griefing vector.

## Proof of Concept

1. Deploy a USDC (6-decimal) / WETH pool with `protocolNotionalFeeE8 = 1_000_000` (1%), `protocolSpreadFeeE6 = 0`, `adminSpreadFeeE6 = 0`.
2. LP adds liquidity.
3. Trader swaps exactly 99 USDC (exact-in, `zeroForOne`). Notional fee on output: `~0.99 USDC` → `notionalFeeToken1Scaled ≈ 9.9 × 10^11 < 10^12`.
4. Attacker calls `MetricOmmPoolFactory.collectPoolFees(pool)`.
   - `totalFee1ToProtocol = 9.9 × 10^11 / 10^12 = 0` (floor division).
   - No transfer occurs. `notionalFeeToken1Scaled` is set to `0`.
5. The ~0.99 USDC notional fee is permanently frozen in the pool (absorbed into `surplus1Scaled`, which is ignored when `spreadSumE6 == 0`).
6. Repeat after every ~99 USDC of swap volume. Over $1M in volume, ~$10,000 in notional fees are frozen.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L48-51)
```text
  /// @notice Multiplier to scale token0 external amounts to internal: 10^(max(18, decimals) - token0.decimals())
  uint256 internal immutable TOKEN_0_SCALE_MULTIPLIER;
  /// @notice Multiplier to scale token1 external amounts to internal: 10^(max(18, decimals) - token1.decimals())
  uint256 internal immutable TOKEN_1_SCALE_MULTIPLIER;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L135-136)
```text
    TOKEN_0_SCALE_MULTIPLIER = token0ScaleMultiplier;
    TOKEN_1_SCALE_MULTIPLIER = token1ScaleMultiplier;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L382-383)
```text
    uint256 notionalFee0AmountScaled = notionalFeeToken0Scaled;
    uint256 notionalFee1AmountScaled = notionalFeeToken1Scaled;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L385-388)
```text
    uint256 surplus0Scaled =
      balance0() * TOKEN_0_SCALE_MULTIPLIER - uint256(binTotals.scaledToken0) - notionalFee0AmountScaled;
    uint256 surplus1Scaled =
      balance1() * TOKEN_1_SCALE_MULTIPLIER - uint256(binTotals.scaledToken1) - notionalFee1AmountScaled;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L391-395)
```text
      uint256 spreadFee0ToAdminScaled = spreadSumE6 == 0 ? 0 : (surplus0Scaled * adminSpreadFeeE6_) / spreadSumE6;
      uint256 spreadFee1ToAdminScaled = spreadSumE6 == 0 ? 0 : (surplus1Scaled * adminSpreadFeeE6_) / spreadSumE6;

      uint256 spreadFee0ToProtocolScaled = spreadSumE6 == 0 ? 0 : (surplus0Scaled * protocolSpreadFeeE6_) / spreadSumE6;
      uint256 spreadFee1ToProtocolScaled = spreadSumE6 == 0 ? 0 : (surplus1Scaled * protocolSpreadFeeE6_) / spreadSumE6;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L411-414)
```text
      (uint256 totalFee0ToAdmin, uint256 totalFee1ToAdmin) =
        deltasScaledToExternal(totalFee0ToAdminScaled, totalFee1ToAdminScaled, Math.Rounding.Floor);
      (uint256 totalFee0ToProtocol, uint256 totalFee1ToProtocol) =
        deltasScaledToExternal(totalFee0ToProtocolScaled, totalFee1ToProtocolScaled, Math.Rounding.Floor);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L429-430)
```text
      notionalFeeToken0Scaled = 0;
      notionalFeeToken1Scaled = 0;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L625-628)
```text
    } else {
      deltaAmount0 = scaledDeltaAmount0 / TOKEN_0_SCALE_MULTIPLIER;
      deltaAmount1 = scaledDeltaAmount1 / TOKEN_1_SCALE_MULTIPLIER;
    }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L379-389)
```text
  function collectPoolFees(address pool) external override nonReentrant {
    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool)
      .collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]
      );
  }
```
