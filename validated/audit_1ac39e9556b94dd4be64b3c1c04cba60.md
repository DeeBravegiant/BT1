### Title
`addLiquidity` Accepts Pool's Own Address as `owner`, Permanently Locking Deposited Tokens — (`metric-core/contracts/MetricOmmPool.sol`, `metric-core/contracts/libraries/LiquidityLib.sol`)

---

### Summary

`addLiquidity` accepts an arbitrary `owner` address with no check that `owner != address(this)`. If a caller passes the pool's own address as `owner`, the resulting LP position is keyed to the pool itself. Because `removeLiquidity` enforces `msg.sender == owner`, and the pool has no mechanism to call `removeLiquidity` on itself, the deposited tokens are permanently locked inside the pool's bin accounting.

---

### Finding Description

`MetricOmmPool.addLiquidity` forwards the caller-supplied `owner` directly to `LiquidityLib.addLiquidity`, which records the position under `_positionBinShares[keccak256(abi.encode(owner, salt, binIdx))]` and credits the deposited amounts to `binState.token0BalanceScaled / token1BalanceScaled` and `binTotals`.

```
addLiquidity(owner=address(pool), salt, deltas, callbackData, extensionData)
  → LiquidityLib.addLiquidity(ctx, owner=pool, ...)
      posKey = keccak256(abi.encode(pool, salt, binIdx))   // position owned by pool
      positionBinShares[posKey] += sharesToAdd             // shares credited to pool
      binState.token0BalanceScaled += amount0Scaled        // tokens enter bin accounting
      binTotals.scaledToken0 += totalToken0ToAddScaled
      callback → caller transfers real tokens into pool    // tokens physically arrive
```

`removeLiquidity` then enforces:

```solidity
if (msg.sender != owner) revert NotPositionOwner();
```

Because `owner == address(pool)` and the pool has no function that calls `removeLiquidity` on itself, the LP shares and the underlying tokens are irrecoverable. The pool has no self-call path through any extension hook or factory function that would satisfy `msg.sender == pool`.

The `_validatePoolParameters` check in the factory only guards `token0 == address(0)`, `token1 == address(0)`, and `token0 == token1`; there is no analogous guard in `addLiquidity` for the `owner` parameter.

---

### Impact Explanation

Any caller who passes `owner = address(pool)` loses the tokens they deposit via the `metricOmmModifyLiquidityCallback`. Those tokens enter `binTotals` and individual `BinState` balances permanently — they participate in future swaps but the LP claim against them can never be redeemed. This is a direct, irreversible loss of the depositor's principal. Additionally, the permanently locked shares inflate `_binTotalShares[binIdx]`, diluting the pro-rata entitlement of all other LPs in that bin on future `removeLiquidity` calls.

---

### Likelihood Explanation

The trigger is unprivileged: any external account can call `addLiquidity` with `owner = address(pool)`. A user could do this accidentally (e.g., a router that passes `address(this)` as owner when `address(this)` happens to be the pool), or an attacker could craft a transaction to grief a specific pool. The Cally M-02 analog was rated Medium precisely because it requires a "precise and niche mistake" — the same characterization applies here.

---

### Recommendation

Add a guard at the top of `addLiquidity` (or inside `LiquidityLib.addLiquidity`) rejecting the pool's own address as `owner`:

```solidity
// In MetricOmmPool.addLiquidity, before delegating to LiquidityLib:
if (owner == address(this)) revert InvalidOwner();
```

Alternatively, enforce this inside `LiquidityLib.addLiquidity` using `address(this)` (which resolves to the pool under DELEGATECALL):

```solidity
if (owner == address(this)) revert IMetricOmmPoolActions.InvalidOwner();
```

---

### Proof of Concept

1. Pool is deployed at address `P` with `TOKEN0 = USDC`, `TOKEN1 = WETH`.
2. Attacker (or a misconfigured router) calls:
   ```
   P.addLiquidity(
       owner    = address(P),   // pool's own address
       salt     = 0,
       deltas   = { binIdxs: [0], shares: [minimalMintableLiquidity] },
       callbackData = ...,
       extensionData = ""
   )
   ```
3. Inside `LiquidityLib.addLiquidity`, `posKey = keccak256(abi.encode(P, 0, 0))` is written with the minted shares. `binState.token0BalanceScaled` and `binTotals.scaledToken0` are incremented. The callback fires and the caller transfers, say, 1000 USDC into the pool.
4. Attacker (or anyone) attempts:
   ```
   P.removeLiquidity(owner = address(P), salt = 0, deltas = ..., "")
   ```
   → reverts with `NotPositionOwner()` because `msg.sender != address(P)`.
5. No other code path in `MetricOmmPool` or `MetricOmmPoolFactory` can satisfy `msg.sender == address(P)` for a `removeLiquidity` call.
6. The 1000 USDC is permanently locked. `_binTotalShares[0]` is inflated by `minimalMintableLiquidity`, diluting all other LPs' pro-rata share of bin 0.