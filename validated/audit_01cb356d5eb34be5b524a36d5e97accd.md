Audit Report

## Title
LP Principal Permanently Locked When `addLiquidity` Is Called With `owner = address(0)` — (`metric-core/contracts/MetricOmmPool.sol`, `metric-core/contracts/libraries/LiquidityLib.sol`)

## Summary
`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` address with no zero-address guard and passes it directly into `LiquidityLib.addLiquidity`, which mints shares to the position key `keccak256(abi.encode(address(0), salt, bin))`. Because `removeLiquidity` enforces `msg.sender == owner` and `msg.sender` is never `address(0)` in a valid EVM transaction, any tokens deposited under this key are permanently irrecoverable.

## Finding Description
`MetricOmmPool.addLiquidity` (L182–196) performs no zero-address check on `owner` before forwarding it to `LiquidityLib.addLiquidity`:

```solidity
// MetricOmmPool.sol L192-194
(amount0Added, amount1Added) = LiquidityLib.addLiquidity(
  _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
);
```

Inside `LiquidityLib.addLiquidity` (L72), `owner` is used verbatim to derive the position key and credit shares:

```solidity
bytes32 posKey = _positionBinKey(owner, salt, int8(binIdx));
// ...
positionBinShares[posKey] = newUserShares;
```

`_positionBinKey` (L256–258) is `keccak256(abi.encode(owner, salt, bin))`. With `owner = address(0)`, this key is permanently unclaimable.

Tokens are then pulled from `msg.sender` via the modify-liquidity callback (L144–155) and credited to `binState.token0BalanceScaled`/`token1BalanceScaled` and `binTotals`.

The only recovery path is `removeLiquidity` (L206), which hard-requires:

```solidity
if (msg.sender != owner) revert NotPositionOwner();
```

Since `msg.sender` is never `address(0)`, the position key `keccak256(abi.encode(address(0), salt, bin))` is permanently unclaimable and the deposited tokens are locked forever.

The periphery contract `MetricOmmPoolLiquidityAdder` does include a `_validateOwner` guard (L247–248) that reverts on `address(0)`, but this only protects callers routing through the periphery. Any direct caller to the core pool bypasses this check entirely.

## Impact Explanation
The caller's deposited token0 and token1 are permanently locked inside the pool with no recovery path. `binTotals.scaledToken0`/`scaledToken1` and per-bin balances are inflated by the locked amounts but can never be withdrawn. This constitutes a direct, irreversible loss of user principal — a critical/high impact matching the "direct loss of user principal" allowed impact gate.

## Likelihood Explanation
Any unprivileged caller interacting directly with `MetricOmmPool.addLiquidity` (bypassing the periphery) can trigger this by passing `owner = address(0)`. No special role, privileged setup, or malicious token is required. It can occur accidentally (e.g., a misconfigured integration that zero-initialises the owner field) or deliberately as a griefing vector. The `msg.sender` pays the tokens and loses them.

## Recommendation
Add a zero-address guard at the entry of `MetricOmmPool.addLiquidity` (or equivalently at the entry of `LiquidityLib.addLiquidity`):

```solidity
require(owner != address(0), "addLiquidity: owner is zero address");
```

This mirrors the guard already present in `MetricOmmPoolLiquidityAdder._validateOwner` and closes the gap for direct pool callers.

## Proof of Concept
1. Attacker calls directly on the pool (bypassing periphery):
   ```solidity
   pool.addLiquidity(
       address(0),   // owner = zero address
       0,            // salt
       deltas,       // valid bin/share arrays
       callbackData,
       extensionData
   );
   ```
2. `LiquidityLib.addLiquidity` computes `posKey = keccak256(abi.encode(address(0), 0, binIdx))` and credits shares there.
3. The callback fires; `msg.sender` transfers `amount0Added` and `amount1Added` into the pool.
4. `binTotals.scaledToken0`/`scaledToken1` and per-bin balances are incremented.
5. Any subsequent call to `removeLiquidity(address(0), 0, deltas, ...)` reverts at `if (msg.sender != owner) revert NotPositionOwner()` because `msg.sender` cannot be `address(0)`.
6. The deposited tokens are permanently locked with no recovery path.