The code confirms all three pillars of the claim. Let me verify the exact fee accounting path to ensure the LP fee is truly embedded in the bin balance (not tracked separately).Audit Report

## Title
LP Fee Sandwich: Attacker Captures Existing LPs' Swap Fees by Sandwiching Swaps with Proportional Liquidity Add/Remove — (`metric-core/contracts/libraries/LiquidityLib.sol`)

## Summary
LP fees are deposited directly into each bin's token balance during a swap, with no separate per-share fee accumulator. Because `addLiquidity` and `removeLiquidity` both price shares proportionally to the **current** bin balance with no time-lock or fee-on-entry, an attacker can front-run any swap by adding a dominant share of liquidity, let the LP fee land in the bin balance, then immediately back-run with `removeLiquidity` to extract nearly all of the fee. Existing LPs lose the fees they earned.

## Finding Description

**LP fee lands directly in bin balance (no separate accumulator)**

In `SwapMath.buyToken1InBinSpecifiedIn`, the LP fee is the portion of `totalIn0Scaled` not taken as protocol fee, and it is added directly to `binState.token0BalanceScaled`:

```solidity
// SwapMath.sol L779-781
binState.token1BalanceScaled -= out1Scaled.toUint104();
binState.token0BalanceScaled =
  (uint256(binState.token0BalanceScaled) + totalIn0Scaled - protocolFeeAmountScaled).toUint104();
```

There is no per-share fee accumulator (no `feeGrowthInside`, no `feePerShare` tracking). A grep of `LiquidityLib.sol` for `owed`, `owedFee`, `lpFee`, `claimFee`, and `feePerShare` returns zero matches, confirming fees are never tracked separately from principal.

**`addLiquidity` prices new shares against the current (pre-fee) balance**

When a bin already has shares (`binTotalSharesVal != 0`), `LiquidityLib.addLiquidity` computes the required deposit proportionally to the live bin balance:

```solidity
// LiquidityLib.sol L108-110
amount0Scaled = Math.ceilDiv(_checkedMul(binState.token0BalanceScaled, sharesToAdd), binTotalSharesVal);
amount1Scaled = Math.ceilDiv(_checkedMul(binState.token1BalanceScaled, sharesToAdd), binTotalSharesVal);
```

An attacker who calls this before the swap pays at the pre-fee price.

**`removeLiquidity` returns tokens proportionally to the current (post-fee) balance**

```solidity
// LiquidityLib.sol L205-206
uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;
```

After the swap, the bin balance includes the LP fee, so the attacker's removal receives a proportional share of it.

**No protocol-level guard exists**

- `addLiquidity` and `removeLiquidity` are both `external` and permissionless (only `nonReentrant` and `msg.sender == owner` checks).
- Extensions (`_beforeAddLiquidity`, `_beforeRemoveLiquidity`) are optional, pool-specific, and not configured by default. They are not a protocol-level mitigation.
- There is no time-lock, no withdrawal delay, no fee-on-entry, and no snapshot-based fee accounting anywhere in the core liquidity path.

**Exploit flow**

Let bin X have balance `B` token0 and `T` total shares (held by honest LPs). Attacker:

1. **Front-runs** the pending swap: calls `addLiquidity` for `S` shares (`S >> T`), paying `B * S / T` token0 at the pre-fee price.
2. Swap executes: LP fee `F` enters the bin. Bin balance becomes `B + F`, total shares `T + S`.
3. **Back-runs** with `removeLiquidity` for `S` shares, receiving `(B + F) * S / (T + S)` token0.

Attacker profit = `(B + F) * S / (T + S) − B * S / T = F * S * T / (T * (T + S)) ≈ F` when `S >> T`.

Honest LPs receive only `(B + F) * T / (T + S) ≈ 0` of the fee instead of the full `F` they earned.

## Impact Explanation
Existing LP providers suffer a direct, repeatable loss of earned swap fees (owed LP assets). With a flash loan, the attacker can dominate any bin and extract essentially 100% of LP fees from any swap. This makes LP provision economically unviable and constitutes a direct loss of owed LP yield above Sherlock thresholds. The corrupted value is `binState.token0BalanceScaled` (or `token1BalanceScaled`): the LP fee component embedded in it is claimable by the attacker's newly minted shares rather than by the pre-existing LP shares that earned it.

## Likelihood Explanation
Any swap generating a non-zero LP fee (i.e., `spreadFeeE6 < 1e6`) is exploitable. The attacker requires only: (1) MEV capability to front-run and back-run in the same block (standard on Ethereum, Base, HyperEVM); (2) a flash loan source for capital (widely available); (3) no special permissions — `addLiquidity` and `removeLiquidity` are fully permissionless on pools without a `DepositAllowlistExtension`. The attack is repeatable on every swap in every unguarded pool.

## Recommendation
Implement one or more of the following:

1. **Snapshot-based fee accounting**: Track a per-share fee accumulator (analogous to Uniswap v3's `feeGrowthInside`) so fees are attributed only to shares that existed at the time of the swap, not to shares added after.
2. **Fee-on-entry**: When adding liquidity to a non-empty bin, charge the new LP a proportional share of the accrued LP fees (the difference between current bin balance and the "principal" balance), preventing entry at the pre-fee price.
3. **Withdrawal delay / time-lock**: Require a minimum holding period (e.g., one block) between `addLiquidity` and `removeLiquidity` for the same position, preventing same-block sandwich attacks.

## Proof of Concept

```
State: Bin 0, token0BalanceScaled = 10,000, binTotalShares = 1,000 (honest LP)
LP fee per swap ≈ 100 token0 (1% spread, no protocol cut)

Step 1 — Attacker front-runs swap:
  addLiquidity(bin=0, shares=99,000)
  Cost = 10,000 * 99,000 / 1,000 = 990,000 token0   [LiquidityLib.sol L109]
  New state: token0BalanceScaled=1,000,000, binTotalShares=100,000

Step 2 — Swap executes:
  LP fee = 100 token0 → token0BalanceScaled = 1,000,100   [SwapMath.sol L780-781]

Step 3 — Attacker back-runs:
  removeLiquidity(bin=0, shares=99,000)
  Receives = 1,000,100 * 99,000 / 100,000 = 990,099 token0   [LiquidityLib.sol L205]

Attacker profit = 990,099 − 990,000 = 99 token0 (99% of the 100 token0 LP fee)
Honest LP receives = 1,000,100 * 1,000 / 100,000 = 10,001 token0 (only 1 token0 of the fee)
```

Foundry test plan: deploy a pool with no extensions, seed bin 0 with honest LP shares, simulate the three-step sandwich in a single test block using `vm.prank` for the attacker and a mock flash-loan callback, and assert the attacker's net token0 gain equals approximately `F * S / (T + S)` while the honest LP's share of the fee is reduced by the same amount.