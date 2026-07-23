All cited code is confirmed accurate. Let me verify the factory `collectTokens` scope to complete the validation.

Audit Report

## Title
Permanent LP Fund Lock via Operator Pattern: `addLiquidity` Accepts Arbitrary `owner` but `removeLiquidity` Enforces Hard `msg.sender == owner` With No Delegation or Rescue Path — (File: metric-core/contracts/MetricOmmPool.sol)

## Summary
`addLiquidity` explicitly supports an operator pattern where `msg.sender` pays but any arbitrary `owner` address receives the position. `removeLiquidity` enforces a hard `msg.sender == owner` check with no delegation, no position-transfer mechanism, and no factory-level rescue path for pool-held LP tokens. If `owner` is set to a contract that cannot itself call `removeLiquidity`, the deposited tokens are permanently locked in the pool with no recovery path.

## Finding Description
`addLiquidity` in `MetricOmmPool.sol` accepts an arbitrary `owner` parameter and records the position under `keccak256(abi.encode(owner, salt, bin))`. The NatSpec explicitly documents: "`msg.sender` pays but need not equal `owner` (operator pattern)."

`removeLiquidity` enforces a strict identity check before any state is touched:
```solidity
if (msg.sender != owner) revert NotPositionOwner();
```
Tokens are then transferred directly to `owner` inside `LiquidityLib.removeLiquidity` via `IERC20(ctx.token0).safeTransfer(owner, amount0Removed)`.

There is no position-transfer function, no operator-approved-withdrawal path, and no factory rescue for pool-held LP balances. The factory's `collectTokens` operates only on `address(this)` (the factory's own balance), not on individual pool balances. A grep across the entire codebase confirms zero implementations of `transferPosition`, `approveWithdraw`, `setOperator`, `positionDelegate`, or `withdrawOnBehalf`.

The periphery `addLiquidityExactShares(pool, owner, …)` overload validates only `owner != address(0)` before forwarding the arbitrary address to the core pool, providing no protection against inaccessible `owner` addresses.

## Impact Explanation
Any tokens deposited into a position whose `owner` cannot execute `removeLiquidity` are permanently irrecoverable. The pool holds the real ERC-20 balances; `binTotals` and per-bin `token(0|1)BalanceScaled` correctly account for them, but no code path exists to release them without `msg.sender == owner`. The loss is unbounded — an operator can deposit any amount on behalf of an inaccessible address. This constitutes direct, permanent loss of user principal meeting Critical/High severity thresholds.

## Likelihood Explanation
The operator pattern is a first-class, documented feature used by `MetricOmmPoolLiquidityAdder`. Real-world triggers include: a smart-contract wallet or vault that calls `addLiquidityExactShares(pool, vaultAddress, …)` but does not implement a `removeLiquidity` forwarding function; a multisig set as `owner` that later loses quorum or is upgraded to a new address; an off-by-one or copy-paste error supplying the wrong `owner` address in an integration script. None of these require malicious intent; all are plausible production mistakes reachable by any unprivileged operator caller.

## Recommendation
1. Add a position-transfer function (analogous to ERC-721 `transferFrom`) so `owner` can delegate withdrawal rights to a new address.
2. Alternatively, add an operator-approval mapping so `owner` can pre-authorize a delegate to call `removeLiquidity` on their behalf.
3. At minimum, document the irrecoverability risk prominently in `addLiquidity` NatSpec so integrators understand that setting `owner` to a contract requires that contract to be able to call `removeLiquidity`.

## Proof of Concept
```
1. Alice (operator) calls:
   pool.addLiquidity(
       owner  = address(vaultContract),   // vault has no removeLiquidity path
       salt   = 0,
       deltas = {binIdxs: [4], shares: [100_000]},
       ...
   )
   → Alice pays token0; pool records position under keccak256(vaultContract, 0, 4).
   → binState.token0BalanceScaled += amount0Scaled; binTotals.scaledToken0 += amount0Scaled.

2. Alice (or anyone) later calls:
   pool.removeLiquidity(address(vaultContract), 0, deltas, "")
   → Line 206: msg.sender (Alice) != owner (vaultContract) → revert NotPositionOwner()

3. vaultContract itself cannot call removeLiquidity because it has no such function.

4. factory.collectTokens() operates on factory balance only — no rescue for pool-held LP balances.

Result: token0 deposited in step 1 is permanently locked in the pool.
```