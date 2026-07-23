Audit Report

## Title
`LiquidityLib.addLiquidity` unchecked `binTotalShares` overflow corrupts bin share accounting, permanently breaking `removeLiquidity` and enabling future-LP fund theft — (`metric-core/contracts/libraries/LiquidityLib.sol`)

## Summary

The entire body of `LiquidityLib.addLiquidity` executes inside a single `unchecked {}` block. When a bin's token balances are both zero (a normal post-swap state), the only overflow guard — `_checkedMul` — is bypassed because `0 * b == 0` never overflows. An attacker can supply a crafted `sharesToAdd` that wraps `binTotalShares[binIdx]` to zero while paying nothing, permanently bricking `removeLiquidity` for all existing LPs in that bin via division-by-zero, and enabling theft from any future LP who re-enters the bin.

## Finding Description

**Root cause — unchecked arithmetic on share totals:**

The entire function body is wrapped in `unchecked`: [1](#0-0) 

The write that can silently wrap is at line 120, inside that block: [2](#0-1) 

**Why `_checkedMul` does not protect this path:**

`_checkedMul` is defined without an `unchecked` block, so it retains Solidity 0.8 overflow protection: [3](#0-2) 

However, when both `binState.token0BalanceScaled == 0` and `binState.token1BalanceScaled == 0`, the multiplications at lines 109–110 compute `0 * sharesToAdd == 0` — no overflow fires regardless of `sharesToAdd`: [4](#0-3) 

Both `amount0Scaled` and `amount1Scaled` resolve to zero, so the attacker is charged nothing.

**Precondition — empty bin with non-zero shares:**

Swap logic in `MetricOmmPool.sol` drains `token1BalanceScaled` (and `token0BalanceScaled`) directly via `SwapMath` but never modifies `_binTotalShares`. After a bin is fully consumed by swaps, both balances reach zero while `binTotalShares[binIdx]` retains the original LP's share count. This is a routine, expected state confirmed by the swap loop: [5](#0-4) 

**Minimum-liquidity guard is insufficient:**

The only check on `newUserShares` is a lower-bound test: [6](#0-5) 

A wrapped `newUserShares` equal to `type(uint256).max - S + 1` is astronomically large and passes this check silently.

**Exploit flow:**

1. LP adds liquidity to bin `B`. `binTotalShares[B] = S`, `token1BalanceScaled = T > 0`.
2. Normal swap activity fully drains bin `B`: both balances → 0, `binTotalShares[B]` remains `S`.
3. Attacker calls `addLiquidity` with `sharesToAdd = type(uint256).max - S + 1`:
   - `_checkedMul(0, sharesToAdd) = 0` for both legs — no revert, attacker pays zero tokens.
   - `binTotalShares[B] = S + (type(uint256).max - S + 1)` wraps to `0` (unchecked).
   - `positionBinShares[attackerKey] = type(uint256).max - S + 1` (huge stale balance).
4. Any subsequent `removeLiquidity` for bin `B` hits: [7](#0-6) 
   `binTotalSharesVal == 0` → division-by-zero panic → permanent revert for all users of that bin.

**Secondary fund-loss path:**

After corruption, a new LP adding liquidity to bin `B` triggers the `binTotalSharesVal == 0` branch (line 85), paying real tokens and setting `binTotalShares[B] = newShares`. The attacker then calls `removeLiquidity` with their stale `positionBinShares` (never cleared). If attacker's shares exceed `newShares`, the proportional withdrawal drains more than the new LP deposited. The subsequent unchecked subtraction: [8](#0-7) 
underflows `binState.token0BalanceScaled`, further corrupting global `binTotals`.

## Impact Explanation

- **Broken core functionality:** `removeLiquidity` permanently reverts (division-by-zero panic) for all positions in the affected bin — a complete loss of LP withdrawal capability.
- **Direct fund loss:** Future LPs who re-enter the corrupted bin have their deposited principal drained by the attacker's stale `positionBinShares`, with `binTotals` corrupted by the resulting underflow. This meets the Sherlock Critical/High threshold for direct loss of user principal and broken core pool functionality.

## Likelihood Explanation

- Bins being fully consumed by swaps is a routine, expected event in any active pool.
- The attack costs the attacker zero tokens.
- `binTotalShares[binIdx]` is publicly readable via `PoolStateLibrary`/`EXTSLOAD`, so the attacker can compute the exact `sharesToAdd` needed.
- No privileged role is required; `addLiquidity` is a public entry point callable by any address.
- The attack is repeatable across any bin that reaches the zero-balance state.

## Recommendation

Remove the blanket `unchecked {}` wrapper from `addLiquidity`, or add explicit overflow guards before the share writes:

```solidity
uint256 newBinTotal = binTotalSharesVal + sharesToAdd;
if (newBinTotal < binTotalSharesVal) revert SharesOverflow();
binTotalShares[binIdx] = newBinTotal;

uint256 newUserSharesChecked = userShares + sharesToAdd;
if (newUserSharesChecked < userShares) revert SharesOverflow();
positionBinShares[posKey] = newUserSharesChecked;
```

Alternatively, move the `binTotalShares` and `positionBinShares` writes outside the `unchecked` block so Solidity's default overflow protection applies. Also consider adding an upper-bound cap on `sharesToAdd` relative to `type(uint256).max - binTotalSharesVal`.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;
import "forge-std/Test.sol";

contract BinTotalSharesOverflowTest is Test {
    // Setup: deploy pool, add LP to bin -1 (token1 only, below current price)

    function test_binTotalSharesOverflow() public {
        // 1. LP adds liquidity to bin -1
        uint256 lpShares = 1_000_000e18;
        _addLiquidity(lp, -1, lpShares);

        // 2. Drain bin -1 via swap (buy token1, sell token0) until token1BalanceScaled == 0
        _drainBin(-1);

        // Verify precondition: both balances 0, shares non-zero
        (uint104 t0, uint104 t1,,,) = pool.getBinState(-1);
        assertEq(t0, 0);
        assertEq(t1, 0);
        uint256 totalShares = pool.binTotalShares(-1);
        assertGt(totalShares, 0);

        // 3. Attacker calls addLiquidity with overflow sharesToAdd, pays 0 tokens
        uint256 overflowShares = type(uint256).max - totalShares + 1;
        _addLiquidity(attacker, -1, overflowShares);

        // 4. binTotalShares wraps to 0
        assertEq(pool.binTotalShares(-1), 0);

        // 5. LP's removeLiquidity reverts with division-by-zero panic
        vm.expectRevert(); // Panic(0x12): division by zero
        _removeLiquidity(lp, -1, lpShares);
    }
}
```

### Citations

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L51-51)
```text
    unchecked {
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L76-79)
```text
          uint256 newUserShares = userShares + sharesToAdd;
          if (newUserShares < ctx.minimalMintableLiquidity) {
            revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
          }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L109-110)
```text
            amount0Scaled = Math.ceilDiv(_checkedMul(binState.token0BalanceScaled, sharesToAdd), binTotalSharesVal);
            amount1Scaled = Math.ceilDiv(_checkedMul(binState.token1BalanceScaled, sharesToAdd), binTotalSharesVal);
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L120-121)
```text
          binTotalShares[binIdx] = binTotalSharesVal + sharesToAdd;
          positionBinShares[posKey] = newUserShares;
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L205-206)
```text
          uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
          uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L210-213)
```text
          binState.token0BalanceScaled -= uint104(amount0Scaled);
          // forge-lint: disable-next-line(unsafe-typecast)
          binState.token1BalanceScaled -= uint104(amount1Scaled);
          binTotalShares[binIdx] = binTotalSharesVal - sharesToRemove;
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L261-263)
```text
  function _checkedMul(uint256 a, uint256 b) internal pure returns (uint256) {
    return a * b;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1154-1163)
```text
      while (state.amountSpecifiedRemainingScaled > 0) {
        bool nonEmptyBin = true;
        if (binState.token1BalanceScaled == 0 || curPosInBinCache == 0) {
          if (params.priceLimitX64 != 0 && params.priceLimitX64 >= lowerPriceX64) {
            break;
          }
          if (totalAvailableToken1Scaled == 0) {
            break;
          }
          nonEmptyBin = false;
```
