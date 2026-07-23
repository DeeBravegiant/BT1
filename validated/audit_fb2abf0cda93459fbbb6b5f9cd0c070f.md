### Title
Missing Slippage Protection on `removeLiquidity` Allows Sandwich Attacks to Reduce LP Token Output — (`metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`MetricOmmPool.removeLiquidity` accepts a fixed share count to burn but exposes no `minAmount0`/`minAmount1` output guard. Because the tokens returned are computed from live bin balances at execution time, a sandwich attack can drain one token from the victim's bins before the removal executes, causing the LP to receive a worse token composition than expected with no on-chain protection.

---

### Finding Description

`removeLiquidity` in `MetricOmmPool.sol` (lines 199–212) accepts a `LiquidityDelta` (bin indices + share counts) and returns `(amount0Removed, amount1Removed)`. The amounts are computed by `LiquidityLib.removeLiquidity` as a proportional claim on the live `token0BalanceScaled` / `token1BalanceScaled` of each targeted bin at the moment of execution.

```solidity
// metric-core/contracts/MetricOmmPool.sol  L199-L212
function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
{
    ...
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
        _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    ...
}
```

There is no `minAmount0` / `minAmount1` parameter, and no periphery wrapper enforces one. The only periphery contract for liquidity is `MetricOmmPoolLiquidityAdder`, which covers only `addLiquidity`.

**Attack path:**

1. LP submits `removeLiquidity` targeting bins that currently hold a mix of token0 and token1.
2. Attacker front-runs with a large swap (e.g., token1 → token0) through those exact bins, draining token0 from them and filling them with token1.
3. LP's `removeLiquidity` executes: `token0BalanceScaled` in the targeted bins is now much lower, so `amount0Removed` is far less than the LP expected; `amount1Removed` is correspondingly higher.
4. Attacker back-runs, swapping token0 → token1 to restore the bin state.

The LP receives a token composition they did not consent to. If they needed token0 (e.g., to repay a loan), they suffer a real economic loss.

**Contrast with `addLiquidity`:** `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` enforces `maxAmountToken0` / `maxAmountToken1` caps in the callback (line 165), and `addLiquidityWeighted` uses a probe-then-pay pattern with cursor-bound checks. No equivalent protection exists on the removal side.

---

### Impact Explanation

Direct loss of LP principal. The LP burns a fixed number of shares but receives fewer tokens of the desired denomination than the fair bin-proportional value at submission time. The loss is bounded by the depth of the attacker's swap relative to the bin balances, but can be material for large positions or thin bins.

---

### Likelihood Explanation

`removeLiquidity` is callable by any position owner directly on the pool (enforced by `msg.sender != owner` revert at line 206). Transactions are visible in public mempools on all target chains. The attacker pays spread fees on both legs of the sandwich, so the attack is not straightforwardly profitable — it is a griefing vector. However, an attacker who also holds a competing LP position in the same bins can benefit indirectly (their remaining shares claim a better composition after the victim exits). Likelihood is **medium-low** but the impact when triggered is direct and measurable.

---

### Recommendation

1. **Add slippage parameters to `removeLiquidity`:**

```solidity
function removeLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 minAmount0,   // <-- new
    uint256 minAmount1,   // <-- new
    bytes calldata extensionData
) external ... returns (uint256 amount0Removed, uint256 amount1Removed) {
    ...
    if (amount0Removed < minAmount0 || amount1Removed < minAmount1)
        revert InsufficientOutput(amount0Removed, amount1Removed);
}
```

2. **Alternatively**, create a periphery `MetricOmmPoolLiquidityRemover` wrapper (mirroring `MetricOmmPoolLiquidityAdder`) that enforces minimum output amounts after calling the pool's `removeLiquidity`.

---

### Proof of Concept

```
Setup:
  - Pool has bin 0 with 1000 token0 and 1000 token1 (scaled).
  - LP owns 50% of bin 0 shares → fair removal ≈ 500 token0 + 500 token1.

Step 1 (front-run):
  Attacker calls pool.swap(zeroForOne=false, amountSpecified=large)
  → swaps token1 into bin 0, draining token0.
  → bin 0 now holds ~100 token0 and ~1900 token1.

Step 2 (victim tx):
  LP calls pool.removeLiquidity(owner, salt, deltas=[{bin:0, shares:50%}], extensionData)
  → amount0Removed = 50 token0   (was 500)
  → amount1Removed = 950 token1  (was 500)
  → LP receives 450 fewer token0 than expected; no revert occurs.

Step 3 (back-run):
  Attacker swaps token0 back for token1, restoring bin state.
  Attacker net cost: 2× spread fee on the round-trip swap.
  LP net loss: 450 token0 worth of value (at oracle price) if token1 is worth less to them.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-167)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }
```
