### Title
Protocol Spread-Fee and Notional-Fee Calculations Use Floor Division, Causing Systematic Undercollection of Protocol Revenue - (`metric-core/contracts/libraries/SwapMath.sol`, `metric-core/contracts/MetricOmmPool.sol`)

### Summary

Every swap in Metric OMM computes the protocol's share of the spread fee and the notional fee using plain Solidity integer division (`/`), which truncates toward zero. Because both quantities are always non-negative, truncation is equivalent to floor division. The result is that the protocol collects strictly less than its entitled share on every swap that produces a non-zero remainder, while the LP or the trader retains the dust. The pattern is the direct structural analog of M-3: a fee/debt quantity is divided with the wrong rounding direction, systematically favouring the counterparty over the protocol.

### Finding Description

**Spread-fee path (three call sites in `SwapMath.sol`):** [1](#0-0) 

```solidity
uint256 feeAmountScaled = Math.ceilDiv(amountInScaled * currBinBuyFeeX64, ONE_X64);
amountInScaled += feeAmountScaled;
uint256 protocolFeeAmountScaled = (feeAmountScaled * spreadFeeE6) / 1e6;   // ← floor
```

The LP fee (`feeAmountScaled`) is correctly computed with `Math.ceilDiv` (rounds up, favours protocol). However, the protocol's share of that fee is then split with plain `/`, which floors the result. The LP receives `feeAmountScaled - protocolFeeAmountScaled`; because `protocolFeeAmountScaled` is rounded down, the LP silently absorbs the remainder that should belong to the protocol. The same pattern appears in the exact-output buy-token1 path and the sell-token0 path: [2](#0-1) [3](#0-2) 

**Notional-fee path (four call sites in `MetricOmmPool.sol`):** [4](#0-3) 

```solidity
uint256 notionalFeeScaled = uint256(-amount1DeltaScaled) * notionalFeeE8 / 1e8;  // ← floor
if (notionalFeeScaled > 0) {
    amount1DeltaScaled = amount1DeltaScaled + int256(notionalFeeScaled);
    notionalFeeToken1Scaled = (uint256(notionalFeeToken1Scaled) + notionalFeeScaled).toUint128();
}
```

`notionalFeeScaled` is the fee deducted from the trader's output (or added to the trader's input). Floor division makes `notionalFeeScaled` smaller than the exact value, so the trader receives more output (or pays less input) than the protocol is entitled to charge. The same floor division appears for the token0 exact-in case, and both exact-out cases: [5](#0-4) [6](#0-5) 

The codebase already contains `SignedMath.ceilDiv` and uses `Math.ceilDiv` / `Math.Rounding.Ceil` consistently everywhere rounding must favour the protocol (LP-fee computation, `addLiquidity` token demand, `invertPriceX64`, `baseFeeX64` derivation). The fee-split and notional-fee lines are the only places where this discipline is broken. [7](#0-6) [8](#0-7) 

### Impact Explanation

On every swap with a non-zero remainder in either division, the protocol collects one fewer scaled unit of fee than it is owed. The loss per swap is bounded by `max(spreadFeeE6, notionalFeeE8) - 1` in the numerator, so at most one scaled unit per call site per swap. Across high-frequency trading the shortfall accumulates monotonically in `notionalFeeToken{0,1}Scaled` and in the spread-fee surplus. Because the loss is one-directional and requires no special state, any normal swap triggers it; no privileged access or malicious setup is needed.

### Likelihood Explanation

Triggered unconditionally on every swap that produces a non-zero remainder (the overwhelming majority of real swaps). No attacker action is required; the loss accrues passively to every LP and every trader who interacts with the pool.

### Recommendation

Replace the two floor-division patterns with ceiling division:

```diff
// SwapMath.sol – spread fee split (repeat for all three call sites)
- uint256 protocolFeeAmountScaled = (feeAmountScaled * spreadFeeE6) / 1e6;
+ uint256 protocolFeeAmountScaled = Math.ceilDiv(feeAmountScaled * spreadFeeE6, 1e6);

// MetricOmmPool.sol – notional fee (repeat for all four call sites)
- uint256 notionalFeeScaled = uint256(-amount1DeltaScaled) * notionalFeeE8 / 1e8;
+ uint256 notionalFeeScaled = Math.ceilDiv(uint256(-amount1DeltaScaled) * notionalFeeE8, 1e8);
```

This mirrors the existing `Math.ceilDiv` usage for the LP-fee computation and is consistent with the protocol's stated rounding policy of always rounding in favour of the pool.

### Proof of Concept

Concrete numeric example for the spread-fee path:

```
feeAmountScaled = 1_000_001
spreadFeeE6     = 500_000   (50 %)

floor:  (1_000_001 * 500_000) / 1_000_000 = 500_000   ← protocol receives
ceil:   ceil(1_000_001 * 500_000 / 1_000_000) = 500_001 ← correct entitlement

Loss per swap = 1 scaled unit
```

For the notional-fee path with `notionalFeeE8 = 1_000_000` (1 %):

```
-amount1DeltaScaled = 1_000_000_099   (output before fee)

floor:  1_000_000_099 * 1_000_000 / 100_000_000 = 10_000_000  ← fee collected
ceil:   ceil(...)                                = 10_000_001  ← correct fee

Trader receives 1 extra scaled unit of token1 per swap.
```

Both losses are dust per swap but accumulate monotonically with swap volume, directly reducing `notionalFeeToken{0,1}Scaled` and the protocol's spread-fee surplus below their correct values.

### Citations

**File:** metric-core/contracts/libraries/SwapMath.sol (L71-71)
```text
    baseFeeX64 = Math.mulDiv(askPriceX64, ONE_X64, midPriceX64, Math.Rounding.Ceil) - ONE_X64;
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L409-411)
```text
      uint256 feeAmountScaled = Math.ceilDiv(amountInScaled * currBinBuyFeeX64, ONE_X64);
      amountInScaled += feeAmountScaled;
      uint256 protocolFeeAmountScaled = (feeAmountScaled * spreadFeeE6) / 1e6;
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L636-638)
```text
      uint256 token1FeeScaled = lpFeeScaledFromGrossInput(totalIn1Scaled, currBinBuyFeeX64, onePlusBuyFeeX64);

      uint256 protocolFeeAmountScaled = (token1FeeScaled * spreadFeeE6) / 1e6;
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L775-777)
```text
      uint256 token0FeeScaled = lpFeeScaledFromGrossInput(totalIn0Scaled, currBinSellFeeX64, onePlusSellFeeX64);

      uint256 protocolFeeAmountScaled = (token0FeeScaled * spreadFeeE6) / 1e6;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L756-761)
```text
            uint256 notionalFeeScaled = uint256(-amount1DeltaScaled) * notionalFeeE8 / 1e8;
            if (notionalFeeScaled > 0) {
              // safe because notionalFeeScaled is bounded by uint128
              // forge-lint: disable-next-line(unsafe-typecast)
              amount1DeltaScaled = amount1DeltaScaled + int256(notionalFeeScaled);
              notionalFeeToken1Scaled = (uint256(notionalFeeToken1Scaled) + notionalFeeScaled).toUint128();
```

**File:** metric-core/contracts/MetricOmmPool.sol (L764-771)
```text
            // safe because amount0DeltaScaled is bounded by uint128 total scaled token0 in bins.
            // forge-lint: disable-next-line(unsafe-typecast)
            uint256 notionalFeeScaled = uint256(-amount0DeltaScaled) * notionalFeeE8 / 1e8;
            if (notionalFeeScaled > 0) {
              // safe because notionalFeeScaled is bounded by uint128
              // forge-lint: disable-next-line(unsafe-typecast)
              amount0DeltaScaled = amount0DeltaScaled + int256(notionalFeeScaled);
              notionalFeeToken0Scaled = (uint256(notionalFeeToken0Scaled) + notionalFeeScaled).toUint128();
```

**File:** metric-core/contracts/MetricOmmPool.sol (L776-784)
```text
          if (zeroForOne) {
            uint256 notionalFeeScaled = feeExclusiveInputScaled * notionalFeeE8 / 1e8;
            if (notionalFeeScaled > 0) {
              // safe because notionalFeeScaled is bounded by uint128
              // forge-lint: disable-next-line(unsafe-typecast)
              amount0DeltaScaled = amount0DeltaScaled + int256(notionalFeeScaled);
              notionalFeeToken0Scaled = (uint256(notionalFeeToken0Scaled) + notionalFeeScaled).toUint128();
            }
          } else {
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L109-110)
```text
            amount0Scaled = Math.ceilDiv(_checkedMul(binState.token0BalanceScaled, sharesToAdd), binTotalSharesVal);
            amount1Scaled = Math.ceilDiv(_checkedMul(binState.token1BalanceScaled, sharesToAdd), binTotalSharesVal);
```
