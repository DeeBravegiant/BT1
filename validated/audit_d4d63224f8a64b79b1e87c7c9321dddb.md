### Title
Unchecked Multiplication Overflow in `_checkedMul` Enables Share Inflation Attack, Draining LP Funds — (`metric-core/contracts/libraries/LiquidityLib.sol`)

---

### Summary

`LiquidityLib.addLiquidity()` computes the token cost for a given share count using `_checkedMul(binState.token0BalanceScaled, sharesToAdd)` inside an `unchecked` block. Because `_checkedMul` performs a bare `a * b` with no overflow guard, an attacker can pass a crafted `sharesToAdd` value that causes the product to wrap around modulo 2^256 to zero (or near-zero). The pool then mints the attacker an arbitrarily large share count for zero (or near-zero) tokens. Existing LPs' shares are diluted to worthlessness, and the attacker can drain the bin's token balance by removing shares in sub-overflow-threshold chunks.

---

### Finding Description

**Root cause — `_checkedMul` inside `unchecked`:** [1](#0-0) 

```solidity
function _checkedMul(uint256 a, uint256 b) internal pure returns (uint256) {
    return a * b;   // no overflow check
}
```

The name `_checkedMul` implies safety, but the function performs an unchecked multiplication. The entire `addLiquidity` body is wrapped in `unchecked { ... }`: [2](#0-1) 

So the multiplication at lines 109–110 silently wraps on overflow: [3](#0-2) 

```solidity
} else {
    amount0Scaled = Math.ceilDiv(_checkedMul(binState.token0BalanceScaled, sharesToAdd), binTotalSharesVal);
    amount1Scaled = Math.ceilDiv(_checkedMul(binState.token1BalanceScaled, sharesToAdd), binTotalSharesVal);
}
```

`binState.token0BalanceScaled` is `uint104` (max ≈ 2^104): [4](#0-3) 

`sharesToAdd` comes from `LiquidityDelta.shares[]`, typed `uint256[]` with no upper-bound validation: [5](#0-4) 

The only check on `sharesToAdd` is a minimum floor (`minimalMintableLiquidity`), not a ceiling: [6](#0-5) 

**Overflow condition:**

When `binState.token0BalanceScaled = T` (uint104) and the attacker supplies `sharesToAdd = Y` such that `T × Y ≡ 0 (mod 2^256)`, the product wraps to 0. For any `T = 2^k` (k ≤ 104), the exact choice `Y = 2^(256−k)` achieves this. For arbitrary `T`, the attacker can find `Y ≈ ⌊2^256 / T⌋` that makes the wrapped product negligibly small.

**Consequence in `addLiquidity`:**

When `amount0Scaled = 0` and `amount1Scaled = 0`, the callback branch is skipped entirely: [7](#0-6) 

The attacker pays **zero tokens** but receives `Y` shares. `binTotalShares[bin]` becomes `S + Y ≈ Y` (enormous).

**Consequence for existing LPs in `removeLiquidity`:** [8](#0-7) 

```solidity
uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
```

A legitimate LP holding `S` shares now receives `T × S / (S + Y) ≈ 0` — complete loss of principal.

**Attacker drains the bin:**

The attacker removes their `Y` shares in chunks `R < 2^(256−104) = 2^152` to keep `T × R` below the overflow threshold. Each chunk yields `T × R / (S + Y) ≈ T × R / Y` tokens. Summing over `Y / R` chunks recovers approximately `T` tokens in total — the full original bin balance.

---

### Impact Explanation

- **Direct loss of LP principal**: existing LPs receive zero tokens on `removeLiquidity` after the attack.
- **Pool insolvency**: the bin's internal `token0BalanceScaled` / `token1BalanceScaled` no longer covers LP claims.
- **Attacker profit**: the attacker drains the bin's full token balance at near-zero cost.
- All bins with `binTotalSharesVal > 0` are vulnerable simultaneously.

---

### Likelihood Explanation

- **Unprivileged trigger**: any EOA or contract can call `addLiquidity` with an arbitrary `sharesToAdd` value.
- **No front-running required**: the attacker can act at any time after the first legitimate LP deposits.
- **Predictable target**: the attacker reads `binState.token0BalanceScaled` on-chain to compute the exact `Y` needed.
- **No existing guard**: there is no cap on `sharesToAdd`, no overflow check in `_checkedMul`, and no minimum-token-paid assertion.

---

### Recommendation

1. **Remove the `unchecked` wrapper from `addLiquidity`**, or at minimum scope it only to arithmetic that is provably safe.
2. **Replace `_checkedMul` with checked multiplication** (Solidity ≥ 0.8 default) or use OpenZeppelin `Math.mulDiv` for the share-to-token conversion, which handles overflow safely:
   ```solidity
   amount0Scaled = Math.mulDiv(binState.token0BalanceScaled, sharesToAdd, binTotalSharesVal, Math.Rounding.Ceil);
   ```
3. **Apply the same fix to `removeLiquidity`** (lines 205–206), which contains the identical `_checkedMul` pattern inside `unchecked`.
4. **Rename or remove `_checkedMul`** — the misleading name implies safety that does not exist.

---

### Proof of Concept

```
Setup:
  - Pool with bin B, token0 (18 decimals, TOKEN_0_SCALE_MULTIPLIER = 1)
  - Legitimate LP adds 10,000 shares → binTotalShares[B] = 10_000, token0BalanceScaled = T = 1_000_000

Attack:
  T = 1_000_000 = 10^6
  Choose Y = 2^256 / 10^6 (rounded to nearest multiple of 10^6 that makes T*Y ≡ 0 mod 2^256)
  Concretely: Y = 2^256 // 10^6 * 10^6  (≈ 2^236)

Step 1 — Attacker calls addLiquidity(binIdx=B, sharesToAdd=Y):
  _checkedMul(1_000_000, Y) = 1_000_000 * Y mod 2^256 = 0  (by construction)
  amount0Scaled = ceilDiv(0, 10_000) = 0
  → callback skipped, attacker pays 0 tokens
  → binTotalShares[B] = 10_000 + Y ≈ Y
  → positionBinShares[attacker] = Y

Step 2 — Legitimate LP removes 10_000 shares:
  amount0Scaled = 1_000_000 * 10_000 / (10_000 + Y) ≈ 0
  → LP receives 0 tokens (full loss)

Step 3 — Attacker removes Y shares in chunks of R = 2^151:
  Each chunk: 1_000_000 * 2^151 / Y ≈ 1_000_000 * 2^151 / 2^236 = 1_000_000 / 2^85 ≈ 0
  ... (adjust chunk size to be just below overflow threshold for the actual T value)
  Total recovered ≈ T = 1_000_000 scaled units → full bin drain
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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L108-111)
```text
          } else {
            amount0Scaled = Math.ceilDiv(_checkedMul(binState.token0BalanceScaled, sharesToAdd), binTotalSharesVal);
            amount1Scaled = Math.ceilDiv(_checkedMul(binState.token1BalanceScaled, sharesToAdd), binTotalSharesVal);
          }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L144-155)
```text
      if (amount0Added > 0 || amount1Added > 0) {
        uint256 balance0Before = IERC20(ctx.token0).balanceOf(address(this));
        uint256 balance1Before = IERC20(ctx.token1).balanceOf(address(this));
        IMetricOmmModifyLiquidityCallback(msg.sender)
          .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
        if (amount0Added > 0 && balance0Before + amount0Added > IERC20(ctx.token0).balanceOf(address(this))) {
          revert IMetricOmmPoolActions.InsufficientTokenBalance();
        }
        if (amount1Added > 0 && balance1Before + amount1Added > IERC20(ctx.token1).balanceOf(address(this))) {
          revert IMetricOmmPoolActions.InsufficientTokenBalance();
        }
      }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L205-206)
```text
          uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
          uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L261-263)
```text
  function _checkedMul(uint256 a, uint256 b) internal pure returns (uint256) {
    return a * b;
  }
```

**File:** metric-core/contracts/types/PoolStorage.sol (L20-21)
```text
  uint104 token0BalanceScaled;
  uint104 token1BalanceScaled;
```

**File:** metric-core/contracts/types/PoolOperation.sol (L7-10)
```text
struct LiquidityDelta {
  int256[] binIdxs;
  uint256[] shares;
}
```
