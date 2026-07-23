### Title
JIT Liquidity Attack Enables Fee Extraction Without Meaningful Liquidity Provision — (File: `metric-core/contracts/libraries/LiquidityLib.sol`)

---

### Summary

`LiquidityLib.addLiquidity` and `LiquidityLib.removeLiquidity` impose no minimum holding period or time-lock. LP fees are credited directly into each bin's token balance the moment a swap settles. An MEV actor can atomically sandwich any large swap — `addLiquidity` → swap → `removeLiquidity` — to capture a proportional share of the LP fee with zero impermanent loss, stealing fee revenue that rightfully belongs to long-term liquidity providers.

---

### Finding Description

**Fee accounting model.** Every swap step in `SwapMath.buyToken0InBinSpecifiedIn` / `buyToken1InBinSpecifiedIn` adds the LP-fee portion of the input token directly to `binState.token0BalanceScaled` / `binState.token1BalanceScaled`:

```
binState.token1BalanceScaled =
    uint256(binState.token1BalanceScaled) + totalIn1Scaled - protocolFeeAmountScaled
``` [1](#0-0) 

The LP fee (`token1FeeScaled - protocolFeeAmountScaled`) is permanently embedded in the bin balance at swap time.

**Share-proportional withdrawal.** `removeLiquidity` pays out `binState.tokenXBalanceScaled * sharesToRemove / binTotalSharesVal` — the full current balance, fees included — with no time-weighting:

```
uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;
``` [2](#0-1) 

**No holding-period guard.** Neither `addLiquidity` nor `removeLiquidity` enforces any lock, cooldown, or fee-on-exit: [3](#0-2) [4](#0-3) 

**Attack path (single atomic transaction or two-block sandwich):**

1. Attacker observes a large pending swap targeting bin `b`.
2. Attacker calls `addLiquidity([b], [s])`, paying `ceil(T0 * s / S)` token0 and `ceil(T1 * s / S)` token1 at the pre-swap bin balance. New total shares = `S + s`.
3. The swap executes. LP fee `F` (in the input token) is added to `binState.tokenXBalanceScaled`.
4. Attacker calls `removeLiquidity([b], [s])`, receiving `(T0' * s) / (S+s)` token0 and `(T1' * s) / (S+s)` token1, where `T1'` includes `F`.
5. Net gain ≈ `F * s / (S + s)` in fee-token terms.

Because the oracle price does not change between steps 2 and 4 (same block or same oracle update), the attacker bears **zero impermanent loss** while capturing a real fraction of the LP fee.

---

### Impact Explanation

Existing long-term LPs lose fee revenue proportional to `s / (S + s)` on every sandwiched swap. The stolen amount is real token value (`F * s / (S + s)`) transferred from honest LPs to the attacker. For a pool with `spreadFeeE6 = 3000` (0.3 %) and a 100 000 USDC swap, the LP fee is ≈ 300 USDC; an attacker matching the existing liquidity (`s = S`) captures ≈ 150 USDC per sandwich, far exceeding gas cost on any L2. This is a direct, repeatable loss of owed LP assets.

---

### Likelihood Explanation

- Requires only mempool visibility and a standard MEV bot — no privileged role.
- Profitable on every large swap where `F * s / (S + s) > gas cost`.
- Fully automatable; scales with pool volume.
- No existing on-chain guard prevents it.

---

### Recommendation

1. **Minimum holding period**: record `block.number` or `block.timestamp` at deposit per position key and revert `removeLiquidity` if the elapsed time is below a threshold (e.g., 1 block or 60 seconds).
2. **Fee-on-remove**: charge a small exit fee that decays with holding time, making same-block round-trips unprofitable.
3. **Time-weighted fee accrual**: track fee-per-share snapshots at deposit time and only distribute fees accrued after the LP's entry, analogous to Uniswap v3's `feeGrowthInside` mechanism.

---

### Proof of Concept

**Setup:**
- Bin 0: `token0BalanceScaled = 1 000 000`, `token1BalanceScaled = 1 000 000`, `binTotalShares = 1 000 000` (existing LP).
- Oracle mid-price = 1:1. Spread fee = 0.3 %. Protocol fee share = 0 for simplicity.

**Step 1 — Attacker adds 1 000 000 shares:**
- Pays `ceil(1 000 000 * 1 000 000 / 1 000 000)` = 1 000 000 token0 + 1 000 000 token1.
- New totals: token0 = 2 000 000, token1 = 2 000 000, shares = 2 000 000.

**Step 2 — Large swap: 1 000 000 token1 in, ~997 000 token0 out (0.3 % fee):**
- LP fee ≈ 3 000 token1 stays in bin.
- Bin after: token0 ≈ 1 003 000, token1 ≈ 3 003 000.

**Step 3 — Attacker removes 1 000 000 shares:**
- Receives `1 003 000 * 1 000 000 / 2 000 000` = 501 500 token0.
- Receives `3 003 000 * 1 000 000 / 2 000 000` = 1 501 500 token1.

**Attacker net (at oracle 1:1):**
- Paid: 1 000 000 + 1 000 000 = 2 000 000.
- Received: 501 500 + 1 501 500 = 2 003 000.
- **Profit: 3 000 tokens** (the LP fee that should have gone entirely to the existing LP).

The existing LP's fee share is halved from 3 000 to 1 500 tokens — a direct, measurable loss of owed LP assets with no on-chain remedy.

### Citations

**File:** metric-core/contracts/libraries/SwapMath.sol (L639-641)
```text
      binState.token0BalanceScaled -= out0Scaled.toUint104();
      binState.token1BalanceScaled =
        uint256((binState.token1BalanceScaled) + totalIn1Scaled - protocolFeeAmountScaled).toUint104();
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L40-50)
```text
  function addLiquidity(
    PoolContext memory ctx,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    BinTotals storage binTotals,
    mapping(int256 => BinState) storage binStates,
    mapping(int256 => uint256) storage binTotalShares,
    mapping(bytes32 => uint256) storage positionBinShares
  ) public returns (uint256 amount0Added, uint256 amount1Added) {
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L161-170)
```text
  function removeLiquidity(
    PoolContext memory ctx,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    BinTotals storage binTotals,
    mapping(int256 => BinState) storage binStates,
    mapping(int256 => uint256) storage binTotalShares,
    mapping(bytes32 => uint256) storage positionBinShares
  ) public returns (uint256 amount0Removed, uint256 amount1Removed) {
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L205-206)
```text
          uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
          uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;
```
