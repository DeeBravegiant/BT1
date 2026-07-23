### Title
Read-Only Reentrancy via `Extsload` During Swap and `addLiquidity` Callback Windows Exposes Inconsistent `binTotals` State — (`metric-core/contracts/MetricOmmPool.sol`, `metric-core/contracts/Extsload.sol`)

---

### Summary

`MetricOmmPool` updates `binTotals` (the pool's internal token accounting) **before** issuing the swap or liquidity callback and **before** receiving the caller's input tokens. The `Extsload` contract, which is inherited by the pool and allows any external contract to read arbitrary pool storage slots, carries **no reentrancy guard**. During the callback window the pool's accounting invariant is broken: `binTotals.scaledToken0` (or `scaledToken1`) is inflated relative to the actual token balance. Any external protocol that reads pool state via `Extsload` / `PoolStateLibrary` during this window observes a false picture of pool solvency and can be exploited.

---

### Finding Description

**Swap callback window (zeroForOne = true path):**

`_executeSwap` is called first and immediately writes the post-trade accounting to persistent storage: [1](#0-0) 

After `_executeSwap` returns, the pool sends token1 out to the recipient and then snapshots `balance0Before`: [2](#0-1) 

The callback fires at line 258. At this exact moment:
- `binTotals.scaledToken0` has been **increased** by `amount0DeltaScaled − protocolFeeScaled` (the expected input, not yet received).
- `balance0()` has **not yet increased** (the caller has not paid).
- The invariant `binTotals.scaledToken0 ≈ balance0() × TOKEN_0_SCALE_MULTIPLIER − notionalFeeToken0Scaled − surplus` is **broken**.

**`addLiquidity` callback window:**

`LiquidityLib.addLiquidity` updates every `binState.token0BalanceScaled`, `binState.token1BalanceScaled`, `binTotals.scaledToken0`, `binTotals.scaledToken1`, and the caller's share records **before** issuing the callback: [3](#0-2) 

The callback fires at line 148. At this moment `binTotals` reflects the new liquidity but the actual tokens have not arrived.

**`Extsload` has no reentrancy guard:**

The reentrancy guard's `nonReentrantView` modifier exists precisely to block view-path reads during active actions: [4](#0-3) 

However, `Extsload` (inherited by the pool) exposes raw `sload`/`tload` reads to any external caller with no modifier. The pool's own documentation confirms that `PoolStateLibrary` mirrors the storage layout for EXTSLOAD-based reads used by periphery quoters: [5](#0-4) 

Any external contract — a lending protocol, an oracle aggregator, a periphery quoter — that calls `Extsload` on the pool during the callback window reads `binTotals.scaledToken0` that is inflated by the full pending input amount.

---

### Impact Explanation

An attacker who controls the swap callback can, during that window:

1. Call `Extsload` on the pool and read the inflated `binTotals.scaledToken0`.
2. Present this value to an external lending protocol that uses Metric OMM pool state as a collateral oracle.
3. Borrow against the phantom collateral.
4. Use the borrowed funds to satisfy the swap callback payment.

Net result: the attacker extracts value from the external lending protocol using a transient, artificially inflated pool balance that the pool itself will never actually hold. The pool's own `IncorrectDelta` / `InsufficientTokenBalance` checks are satisfied, so the swap completes normally and leaves no trace in pool state. The loss falls entirely on the external protocol's depositors.

The same window exists in `addLiquidity`, where `binTotals` is inflated before the `metricOmmModifyLiquidityCallback` fires.

---

### Likelihood Explanation

- **Trigger is unprivileged**: any caller can initiate a swap or `addLiquidity`.
- **Window is always open**: every swap and every `addLiquidity` call opens the window; no special pool configuration is required.
- **`Extsload` is the intended external read path**: the codebase explicitly documents `PoolStateLibrary` as the EXTSLOAD reader used by periphery contracts, making it a natural integration target for external protocols.
- **Likelihood is Medium**: exploitation requires a co-deployed external protocol that reads Metric OMM pool state for financial decisions, which is the expected production integration pattern.

---

### Recommendation

1. Apply the `nonReentrantView` modifier (already defined in `MetricReentrancyGuardTransient`) to all `Extsload` entry points so that storage reads revert while any guarded action is active.
2. Alternatively, restructure the swap and liquidity flows to receive input tokens **before** updating `binTotals`, eliminating the inconsistency window entirely (optimistic-transfer → accounting update → callback → verify is the safer ordering).
3. Document in `PoolStateLibrary` and all periphery integrations that pool state read via EXTSLOAD must not be consumed inside a callback context.

---

### Proof of Concept

```
Attacker contract implements IMetricOmmSwapCallback and IExternalLendingOracle.

1. Attacker calls pool.swap(attacker, true /*zeroForOne*/, amountIn, 0, "", "")
   - Pool calls _executeSwap():
       binTotals.scaledToken0 += (amountInScaled - protocolFee)   ← WRITTEN
       binTotals.scaledToken1 -= amountOutScaled                   ← WRITTEN
   - Pool sends token1 to attacker.
   - Pool snapshots balance0Before = balance0().                   ← balance0 unchanged
   - Pool calls attacker.metricOmmSwapCallback(amount0Delta, amount1Delta, "")

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L32-34)
```text
 * @dev External function order follows `IMetricOmmPool` composition: `IMetricOmmPoolActions`, then `IMetricOmmPoolCollectFees`, then `IMetricOmmPoolFactoryActions`.
 * @dev Contract coupling: storage layout and packing are mirrored by `contracts/libraries/PoolStateLibrary.sol`
 *      for EXTSLOAD-based reads. Any storage reorder or repack is a breaking change for EXTSLOAD readers.
```

**File:** metric-core/contracts/MetricOmmPool.sol (L250-263)
```text
    if (zeroForOne) {
      if (amount1Delta < 0) {
        // casting to uint256 is safe because amount1Delta is negative and the ammount of tokens in pool is capped by uint128.max
        // forge-lint: disable-next-line(unsafe-typecast)
        transferToken1(recipient, uint256(-amount1Delta));
      }

      uint256 balance0Before = balance0();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount0Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) > balance0()) {
        revert IncorrectDelta();
      }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L732-748)
```text
      if (zeroForOne) {
        // casting to uint256 is safe because amount0DeltaScaled is positive in zeroForOne flow.
        // forge-lint: disable-next-line(unsafe-typecast)
        binTotals.scaledToken0 =
          (uint256(binTotals.scaledToken0) + uint256(amount0DeltaScaled) - protocolFeeScaled).toUint128(); // forge-lint: disable-line(unsafe-typecast)
        // casting to uint128/uint256 is safe because bin totals remain bounded by uint128-scaled accounting invariants.
        // forge-lint: disable-next-line(unsafe-typecast)
        binTotals.scaledToken1 = uint128(uint256(binTotals.scaledToken1) - uint256(-amount1DeltaScaled));
      } else {
        // casting to uint256 is safe because amount1DeltaScaled is positive in !zeroForOne flow.
        // forge-lint: disable-next-line(unsafe-typecast)
        binTotals.scaledToken1 =
          (uint256(binTotals.scaledToken1) + uint256(amount1DeltaScaled) - protocolFeeScaled).toUint128(); // forge-lint: disable-line(unsafe-typecast)
        // casting to uint128/uint256 is safe because bin totals remain bounded by uint128-scaled accounting invariants.
        // forge-lint: disable-next-line(unsafe-typecast)
        binTotals.scaledToken0 = uint128(uint256(binTotals.scaledToken0) - uint256(-amount0DeltaScaled));
      }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L134-154)
```text
      if (totalToken0ToAddScaled > 0) {
        binTotals.scaledToken0 = (uint256(binTotals.scaledToken0) + totalToken0ToAddScaled).toUint128();
      }
      if (totalToken1ToAddScaled > 0) {
        binTotals.scaledToken1 = (uint256(binTotals.scaledToken1) + totalToken1ToAddScaled).toUint128();
      }

      (amount0Added, amount1Added) =
        _deltasScaledToExternal(totalToken0ToAddScaled, totalToken1ToAddScaled, ctx, Math.Rounding.Ceil);

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
```

**File:** metric-core/contracts/utils/MetricReentrancyGuardTransient.sol (L23-33)
```text
  /// @dev Blocks view functions while any guarded action is active.
  modifier nonReentrantView() {
    _nonReentrantBeforeView();
    _;
  }

  function _nonReentrantBeforeView() private view {
    if (_currentAction() != 0) {
      revert ReentrancyGuardReentrantCall();
    }
  }
```
