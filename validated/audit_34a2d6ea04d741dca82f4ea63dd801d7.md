Audit Report

## Title
Swap and `addLiquidity` Permanently Broken When USDT Transfer Fee Is Non-Zero — (File: `metric-core/contracts/MetricOmmPool.sol`, `metric-core/contracts/libraries/LiquidityLib.sol`)

## Summary
`MetricOmmPool.swap()` and `LiquidityLib.addLiquidity()` verify callback settlement using expected-amount balance checks (`balanceBefore + expectedAmount > balanceAfter`). When USDT has a non-zero transfer fee, the callback delivers `amount - fee` to the pool instead of `amount`, causing `IncorrectDelta` / `InsufficientTokenBalance` to revert on every call. All swaps and liquidity additions are permanently broken for any pool whose token0 or token1 is USDT with an active fee, and `removeLiquidity` silently delivers less than the computed amount to LPs.

## Finding Description
In `MetricOmmPool.swap()`, the `zeroForOne` branch snapshots `balance0Before`, calls the swap callback instructing it to pay `amount0Delta` of token0, then asserts `balance0() >= balance0Before + amount0Delta`:

```solidity
uint256 balance0Before = balance0();
IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) > balance0()) {
    revert IncorrectDelta();
}
``` [1](#0-0) 

`balance0()` and `balance1()` are plain `balanceOf` calls that correctly reflect the fee-reduced balance:

```solidity
function balance0() internal view returns (uint256) {
    return IERC20(TOKEN0).balanceOf(address(this));
}
``` [2](#0-1) 

When USDT's `basisPointsRate` is non-zero, the callback's `transferFrom(payer, pool, amount0Delta)` causes the pool to receive only `amount0Delta - fee`. The condition `balance0Before + amount0Delta > balance0Before + amount0Delta - fee` is always `true`, so `IncorrectDelta` fires deterministically on every swap.

The identical pattern exists in `LiquidityLib.addLiquidity()`:

```solidity
uint256 balance0Before = IERC20(ctx.token0).balanceOf(address(this));
IMetricOmmModifyLiquidityCallback(msg.sender)
    .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
if (amount0Added > 0 && balance0Before + amount0Added > IERC20(ctx.token0).balanceOf(address(this))) {
    revert IMetricOmmPoolActions.InsufficientTokenBalance();
}
``` [3](#0-2) 

For `removeLiquidity`, the pool calls `safeTransfer(owner, amount0Removed)` where `amount0Removed` is the computed full amount, but USDT deducts its fee in transit — the LP receives `amount0Removed - fee` with no revert and no accounting correction, silently losing principal on every withdrawal: [4](#0-3) 

Neither path measures the actual received delta (`balanceAfter - balanceBefore`) and uses that as the settled amount. No existing guard compensates for the fee shortfall.

## Impact Explanation
Any pool whose token0 or token1 is USDT with a non-zero fee becomes completely non-functional for swaps (`IncorrectDelta` on every call) and liquidity additions (`InsufficientTokenBalance` on every call). `removeLiquidity` executes but LPs receive `amount - fee` instead of `amount`, constituting direct, unrecoverable loss of LP principal on every withdrawal. This matches the allowed impact gate: broken core pool functionality causing loss of funds and unusable swap/liquidity flows.

## Likelihood Explanation
USDT's `basisPointsRate` is currently 0 on Ethereum mainnet but the setter is live and callable by Tether's owner at any time without any on-chain precondition. USDT pools are a primary deployment target (confirmed by the repository's own feed configs listing USDT pairs across Ethereum, BSC, Optimism, and Linea). Once the fee is enabled, breakage is immediate and requires no attacker action — any ordinary swap or liquidity call by any unprivileged user triggers it.

## Recommendation
Replace expected-amount checks with actual-received-delta checks in both `MetricOmmPool.swap()` and `LiquidityLib.addLiquidity()`:

```solidity
// swap, zeroForOne branch
uint256 balance0Before = balance0();
IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
uint256 actualReceived = balance0() - balance0Before;
if (amount0Delta > 0 && actualReceived < uint256(amount0Delta)) {
    revert IncorrectDelta();
}
```

Apply the same pattern in `LiquidityLib.addLiquidity()`. For `removeLiquidity`, either measure the actual delivered amount and update internal bin accounting accordingly, or explicitly document and gate-check that fee-on-transfer tokens are unsupported at pool creation time.

## Proof of Concept
1. Deploy a pool with USDT (`0xdAC17F958D2ee523a2206206994597C13D831ec7`) as `token0` and any standard ERC20 as `token1`.
2. Enable USDT's fee via `usdt.setParams(10, 100)` (10 bps, max 100 USDT) — callable by Tether's owner.
3. Seed the pool with liquidity via direct token transfer (bypassing `addLiquidity`).
4. Call `pool.swap(recipient, true, 10_000e6, 0, callbackData, "")` — `zeroForOne`, selling USDT.
5. The router callback executes `usdt.transferFrom(payer, pool, amount0Delta)`; USDT deducts its fee; pool receives `amount0Delta - fee`.
6. `balance0Before + amount0Delta > balance0()` evaluates to `true` → `IncorrectDelta` revert.
7. Repeat step 4 for `addLiquidity` — same revert path via `InsufficientTokenBalance`.
8. Call `removeLiquidity` — succeeds, but LP wallet receives `amount0Removed - fee`; the shortfall is unrecoverable and not reflected in any internal accounting.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L257-263)
```text
      uint256 balance0Before = balance0();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount0Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) > balance0()) {
        revert IncorrectDelta();
      }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L556-558)
```text
  function balance0() internal view returns (uint256) {
    return IERC20(TOKEN0).balanceOf(address(this));
  }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L144-154)
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
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L242-244)
```text
      if (amount0Removed > 0) {
        IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
      }
```
