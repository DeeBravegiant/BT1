### Title
LP Principal Permanently Stuck When Position Owner Is Blacklisted by Pool Token — (`metric-core/contracts/libraries/LiquidityLib.sol`)

---

### Summary

`LiquidityLib.removeLiquidity` transfers redeemed tokens unconditionally to the `owner` address with no alternative recipient and no admin rescue path. If `owner` is blacklisted by a token with transfer restrictions (USDC or USDT, both explicitly in scope), every withdrawal attempt reverts and the LP's principal is permanently locked in the pool.

---

### Finding Description

In `LiquidityLib.removeLiquidity`, after burning shares and updating all bin accounting, the function executes:

```solidity
if (amount0Removed > 0) {
    IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
}
if (amount1Removed > 0) {
    IERC20(ctx.token1).safeTransfer(owner, amount1Removed);
}
``` [1](#0-0) 

The `owner` address is the sole hardcoded recipient. There is no `recipient` parameter, no fallback address, and no admin rescue function anywhere in the pool.

The caller-side enforcement in `MetricOmmPool.removeLiquidity` makes this worse:

```solidity
if (msg.sender != owner) revert NotPositionOwner();
``` [2](#0-1) 

Because `msg.sender` must equal `owner`, no third party (not even the pool admin or factory) can call `removeLiquidity` on the owner's behalf to redirect tokens to a clean address. The pool also has no emergency withdrawal function and no factory-level rescue path.

The `MinimalLiquidity` guard further tightens the trap: a partial withdrawal that would leave residual shares below `MINIMAL_MINTABLE_LIQUIDITY` also reverts, so the LP cannot even partially drain their position. [3](#0-2) 

The `whenNotPaused` modifier applies only to `swap`, not to `removeLiquidity`, so pausing the pool does not create or remove any rescue path. [4](#0-3) 

---

### Impact Explanation

An LP whose address is blacklisted by USDC or USDT (both explicitly in scope per contest rules) will find that every call to `removeLiquidity` reverts at the `safeTransfer` step. Because Solidity reverts roll back all state changes, the LP's shares are never burned — they remain on-chain indefinitely. The LP retains a share balance that correctly entitles them to underlying tokens, but they can never execute the transfer. The tokens are permanently locked in the pool with no recovery mechanism. This is a direct, permanent loss of user principal.

---

### Likelihood Explanation

USDC and USDT both maintain on-chain blacklists enforced at the token level. Blacklisting occurs for regulatory compliance, sanctions enforcement, or fraud response. Any LP who deposits into a USDC/USDT pool and is subsequently blacklisted — regardless of the reason — faces permanent loss. The condition requires no attacker: it is triggered by a standard, documented feature of the in-scope tokens.

---

### Recommendation

1. **Add an optional `recipient` parameter** to `removeLiquidity` so the owner can redirect proceeds to a non-blacklisted address:
   ```solidity
   function removeLiquidity(
       address owner,
       address recipient,   // new: defaults to owner if address(0)
       uint80 salt,
       LiquidityDelta calldata deltas,
       bytes calldata extensionData
   ) external ...
   ```
   Inside `LiquidityLib.removeLiquidity`, replace `safeTransfer(owner, ...)` with `safeTransfer(recipient, ...)`.

2. **Add an admin emergency rescue function** (ideally behind a timelock/multisig) that can transfer any token balance in excess of `binTotals` accounting to a specified address, analogous to the recommendation in the external report.

---

### Proof of Concept

1. Pool is deployed with `token0 = USDC`, `token1 = WETH`.
2. Alice (EOA) calls `addLiquidity` via a router callback, depositing USDC into bin `+4`. Her position is recorded: `positionBinShares[keccak256(alice, salt, 4)] = 10_000`.
3. USDC's issuer blacklists Alice's address for a compliance reason.
4. Alice calls `removeLiquidity(alice, salt, deltas, "")`.
5. `MetricOmmPool` passes `msg.sender == alice == owner` check.
6. `LiquidityLib.removeLiquidity` computes `amount0Removed > 0`, then executes `IERC20(USDC).safeTransfer(alice, amount0Removed)`.
7. USDC's `transfer` reverts because Alice is blacklisted. The entire transaction reverts.
8. Alice's shares remain at `10_000`. She retries — same revert every time.
9. No other address can call `removeLiquidity` for Alice (`NotPositionOwner` revert).
10. Alice's USDC principal is permanently locked in the pool. [5](#0-4) [4](#0-3)

### Citations

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L161-251)
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
    unchecked {
      uint256 length = deltas.binIdxs.length;
      if (length == 0) return (0, 0);

      uint256 totalToken0ToRemoveScaled = 0;
      uint256 totalToken1ToRemoveScaled = 0;

      BinBalanceDelta[] memory binBalanceDeltas = new BinBalanceDelta[](length);

      for (uint256 i = 0; i < length; i++) {
        int256 binIdx = deltas.binIdxs[i];
        uint256 sharesToRemove = deltas.shares[i];

        if (binIdx < ctx.lowestBin || binIdx > ctx.highestBin) {
          revert IMetricOmmPoolActions.InvalidBinIndex(binIdx);
        }
        if (sharesToRemove == 0) continue;

        {
          // safe because -128 <= LOWEST_BIN <= HIGHEST_BIN <= 127 (enforced by factory)
          // forge-lint: disable-next-line(unsafe-typecast)
          bytes32 posKey = _positionBinKey(owner, salt, int8(binIdx));
          uint256 binTotalSharesVal = binTotalShares[binIdx];
          uint256 userShares = positionBinShares[posKey];

          if (userShares < sharesToRemove) {
            revert IMetricOmmPoolActions.InsufficientLiquidity(sharesToRemove, userShares);
          }
          uint256 newUserShares = userShares - sharesToRemove;
          if (newUserShares > 0 && newUserShares < ctx.minimalMintableLiquidity) {
            revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
          }

          BinState storage binState = binStates[binIdx];
          uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
          uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;

          // casting to uint104 is safe because amount0Scaled and amount1Scaled are less than token(0|1)BalanceScaled
          // forge-lint: disable-next-line(unsafe-typecast)
          binState.token0BalanceScaled -= uint104(amount0Scaled);
          // forge-lint: disable-next-line(unsafe-typecast)
          binState.token1BalanceScaled -= uint104(amount1Scaled);
          binTotalShares[binIdx] = binTotalSharesVal - sharesToRemove;
          positionBinShares[posKey] = newUserShares;

          totalToken0ToRemoveScaled += amount0Scaled;
          totalToken1ToRemoveScaled += amount1Scaled;

          binBalanceDeltas[i] = BinBalanceDelta({
            // safe because amount0Scaled is bounded by uint104 bin balances.
            // forge-lint: disable-next-line(unsafe-typecast)
            delta0Scaled: -int256(amount0Scaled),
            // safe because amount1Scaled is bounded by uint104 bin balances.
            // forge-lint: disable-next-line(unsafe-typecast)
            delta1Scaled: -int256(amount1Scaled)
          });
        }
      }

      if (totalToken0ToRemoveScaled > 0) {
        // forge-lint: disable-next-line(unsafe-typecast)
        binTotals.scaledToken0 = uint128(uint256(binTotals.scaledToken0) - totalToken0ToRemoveScaled);
      }
      if (totalToken1ToRemoveScaled > 0) {
        // forge-lint: disable-next-line(unsafe-typecast)
        binTotals.scaledToken1 = uint128(uint256(binTotals.scaledToken1) - totalToken1ToRemoveScaled);
      }

      (amount0Removed, amount1Removed) =
        _deltasScaledToExternal(totalToken0ToRemoveScaled, totalToken1ToRemoveScaled, ctx, Math.Rounding.Floor);

      if (amount0Removed > 0) {
        IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
      }
      if (amount1Removed > 0) {
        IERC20(ctx.token1).safeTransfer(owner, amount1Removed);
      }

      emit IMetricOmmPoolActions.LiquidityRemoved(owner, salt, deltas.binIdxs, binBalanceDeltas, deltas.shares);
    }
  }
```

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
