Audit Report

## Title
No lockup on LP positions enables zero-risk fee sandwiching of bin swaps — (`metric-core/contracts/libraries/LiquidityLib.sol`)

## Summary
`addLiquidity` and `removeLiquidity` impose no time-based restriction on LP positions. LP fees are embedded directly into bin token balances during swap execution via `SwapMath.buyToken0InBinSpecifiedIn` and related functions. Because Metric OMM is oracle-anchored, an attacker can front-run any large swap by depositing into the active bin, capture a proportional share of the LP fee when the swap settles, and immediately withdraw — all in the same block — at effectively zero impermanent-loss risk, diluting fee revenue owed to existing long-term LPs.

## Finding Description
**Fee embedding.** In `SwapMath.buyToken0InBinSpecifiedIn`, the LP fee portion of the input token is added directly to the bin's scaled balance:

```solidity
binState.token1BalanceScaled =
  uint256((binState.token1BalanceScaled) + totalIn1Scaled - protocolFeeAmountScaled).toUint104();
```

The LP fee (`token1FeeScaled - protocolFeeAmountScaled`) stays in the bin and is immediately claimable by any share-holder proportionally. The same pattern appears in `buyToken0InBinSpecifiedOut` (lines 414–415) and the symmetric sell-side functions.

**No lockup.** `addLiquidity` carries only a `nonReentrant` guard and a `_beforeAddLiquidity` hook call. `removeLiquidity` adds only a `msg.sender != owner` check. Neither function has any timestamp, block-number, or epoch restriction. There is no minimum holding period anywhere in the core pool.

**Proportional removal.** When an LP removes shares, they receive:

```solidity
uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;
```

Because the bin balance already includes the LP fee from the swap, the attacker's withdrawal captures their diluted share of that fee.

**Oracle-anchored pricing eliminates impermanent loss.** In Metric OMM, prices are set by the external oracle; the pool does not discover prices from reserves. The attacker's token composition after the swap reflects the oracle-fair exchange rate, so there is no adverse selection loss to offset the captured fee. The attack is therefore purely profitable (net of gas).

**Optional extensions do not close the gap.** The only deposit-gating mechanism is the opt-in `DepositAllowlistExtension` (an address allowlist) or a custom `beforeAddLiquidity` hook. Neither is deployed by default, and neither constitutes a lockup period — a whitelisted attacker can still execute the same sandwich.

## Impact Explanation
Existing LPs who provide continuous liquidity earn less fee revenue than they are owed. For every large swap, a sandwich LP can dilute the fee pool by depositing immediately before and withdrawing immediately after, capturing a share of the LP fee proportional to their injected capital. The existing LPs' share of the fee is reduced by the same proportion. This is a direct loss of owed LP assets. Over time, rational actors will converge on this strategy, making passive LP provision economically unviable and concentrating fee capture among MEV-capable actors.

## Likelihood Explanation
Any actor with mempool visibility (standard on Ethereum mainnet and most EVM chains) can execute this attack. No special privilege, allowlist membership, or protocol knowledge beyond reading public state is required. The attack is profitable whenever the LP fee captured exceeds gas cost, which is true for any swap of meaningful size. The attack is repeatable on every swap.

## Recommendation
1. **Minimum holding period**: Record the block number at which shares were last minted per position key and reject `removeLiquidity` calls within a configurable `minHoldBlocks` window.
2. **Epoch-based withdraw buffer**: Require that shares minted in epoch `N` cannot be redeemed until epoch `N+1` (or a fixed block/time delay).
3. **Fee snapshot accumulator**: Separate LP fee accounting from bin balances (e.g., a per-share fee accumulator similar to Uniswap v3's `feeGrowthInside`). New depositors would only accrue fees from the moment of deposit, not retroactively from pre-deposit swaps.

## Proof of Concept
**Setup:**
- Pool has bin `0` (active bin) with `T0 = 10,000` token0 scaled, `T1 = 0` token1 scaled, `S = 100,000` total shares.
- Honest LP Alice holds all 100,000 shares.
- A large swap is pending in the mempool: trader sells 5,000 token1 to buy token0. At oracle mid-price 1.0 with 1% spread fee, the LP fee ≈ 50 token1 (net of protocol fee).

**Attack steps (single block):**

1. **Attacker front-runs**: calls `addLiquidity` for bin `0` with `s = 100,000` shares. Pays `10,000 * 100,000 / 100,000 = 10,000` token0 (proportional to current bin balance per `LiquidityLib.addLiquidity` line 109). New totals: `T0 = 20,000`, `S = 200,000`.

2. **Swap executes**: `SwapMath.buyToken0InBinSpecifiedIn` embeds the LP fee into `binState.token1BalanceScaled` (line 641). Bin loses ~5,000 token0 and gains ~5,050 token1. New totals: `T0 ≈ 15,000`, `T1 ≈ 5,050`.

3. **Attacker back-runs**: calls `removeLiquidity` for 100,000 shares. `LiquidityLib.removeLiquidity` computes (lines 205–206):
   - token0: `15,000 * 100,000 / 200,000 = 7,500`
   - token1: `5,050 * 100,000 / 200,000 = 2,525`
   - Total value at oracle price 1.0: `7,500 + 2,525 = 10,025` token0 equivalent.
   - **Net profit: ~25 token0** (half the LP fee), at zero impermanent loss risk.

4. **Alice's loss**: Alice's 100,000 shares now represent `7,500` token0 and `2,525` token1 = `10,025` token0 equivalent. Without the attacker, Alice would have received the full `~50` token0 LP fee. She received only `~25`.