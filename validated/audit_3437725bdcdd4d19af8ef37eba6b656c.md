Audit Report

## Title
JIT Liquidity Front-Running Enables LP Fee Siphoning Without Equivalent Risk - (File: `metric-core/contracts/libraries/LiquidityLib.sol`)

## Summary
An attacker can front-run large swaps by adding liquidity to the active bin immediately before execution, capture a proportional share of the LP spread fee that is embedded directly into the bin's token balance at swap time, and immediately remove liquidity in the same or subsequent transaction. No minimum holding period, lock, or time-weighted fee accounting exists anywhere in the liquidity path, making this attack trivially repeatable against any large pending swap.

## Finding Description

**Root cause — fee embedding into bin balance at swap time:**

In `SwapMath.buyToken0InBinSpecifiedIn` (and the three symmetric variants), the LP fee is not tracked in a separate accumulator; it is folded directly into `binState.token1BalanceScaled`:

```solidity
// SwapMath.sol L636-641
uint256 token1FeeScaled = lpFeeScaledFromGrossInput(totalIn1Scaled, currBinBuyFeeX64, onePlusBuyFeeX64);
uint256 protocolFeeAmountScaled = (token1FeeScaled * spreadFeeE6) / 1e6;
binState.token0BalanceScaled -= out0Scaled.toUint104();
binState.token1BalanceScaled =
  uint256((binState.token1BalanceScaled) + totalIn1Scaled - protocolFeeAmountScaled).toUint104();
```

The LP portion (`token1FeeScaled - protocolFeeAmountScaled`) permanently inflates the bin balance before any share accounting occurs.

**Share pricing uses a pre-swap snapshot:**

When a bin already has liquidity, `addLiquidity` prices new shares against the *current* (pre-swap) balance:

```solidity
// LiquidityLib.sol L109-110
amount0Scaled = Math.ceilDiv(_checkedMul(binState.token0BalanceScaled, sharesToAdd), binTotalSharesVal);
amount1Scaled = Math.ceilDiv(_checkedMul(binState.token1BalanceScaled, sharesToAdd), binTotalSharesVal);
```

An attacker who adds shares before the swap pays at the pre-fee balance. After the swap, the bin balance is higher by the LP fee. `removeLiquidity` then pays out at the post-fee balance:

```solidity
// LiquidityLib.sol L205-206
uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;
```

**No guard prevents sequential calls:**

`MetricReentrancyGuardTransient` uses distinct action IDs per function (`ADD_LIQUIDITY`, `SWAP`, `REMOVE_LIQUIDITY`). It only blocks *reentrant* calls within the same action; it does not prevent sequential calls across different actions in the same transaction or block. There is no minimum holding period, no withdrawal lock, and no time-weighted fee accounting anywhere in the codebase.

**Exploit flow:**

1. Attacker observes a large pending swap (e.g., buying token0 with token1) in the mempool.
2. Attacker front-runs with `addLiquidity` to the active bin, paying at the current pre-swap balance ratio.
3. The victim's swap executes; the LP fee is folded into `binState.token1BalanceScaled`, inflating the bin balance proportionally.
4. Attacker back-runs with `removeLiquidity`, receiving their proportional share of the now-inflated balance — including the LP fee — while returning the same token0 they deposited (minus rounding).
5. Net result: attacker extracts LP fee revenue that would have accrued entirely to pre-existing LPs, without having borne any inventory or market risk.

## Impact Explanation
Existing LPs suffer a direct, quantifiable reduction in earned LP fees (owed LP assets). The attacker extracts value proportional to their injected share fraction multiplied by the total LP fee for the swap. For large swaps with meaningful spread fees, this constitutes a material loss of owed LP assets meeting Sherlock Medium/High thresholds. The attack is repeatable on every large swap and requires no privileged access.

## Likelihood Explanation
Any unprivileged address can execute this attack. It requires only: (1) mempool visibility of a pending large swap, (2) capital to add liquidity (or a flash loan), and (3) two sequential pool calls. On chains with public mempools this is trivially automatable. The attack is profitable whenever the captured LP fee exceeds gas cost, which is the case for any swap of meaningful size.

## Recommendation
Implement one or more of the following mitigations:
- **Minimum holding period:** Record the block number at which each position last added shares and reject `removeLiquidity` calls within the same block (or N blocks).
- **Time-weighted fee accounting:** Track fee accrual per share using a fee-growth-per-share accumulator (similar to Uniswap v3's `feeGrowthInside`), so only shares present *before* a swap accrue that swap's fees.
- **Withdrawal fee on same-block exits:** Charge a penalty on liquidity removed within the same block it was added, redistributing it to remaining LPs.

## Proof of Concept
```solidity
// Foundry test sketch
function testJITFeeExtraction() public {
    // Setup: pool with existing LP (alice) holding 1000 shares in bin 0
    // Attacker (bob) observes large pending swap

    // Step 1: bob front-runs — addLiquidity to bin 0 (1000 shares at pre-swap price)
    vm.prank(bob);
    pool.addLiquidity(bob, 0, deltaFor(bin0, 1000 shares), ...);

    // Step 2: large swap executes — LP fee folds into bin0.token1BalanceScaled
    vm.prank(swapper);
    pool.swap(swapper, false, largeAmount, 0, ...);

    // Step 3: bob back-runs — removeLiquidity, receives proportional share of post-fee balance
    vm.prank(bob);
    pool.removeLiquidity(bob, 0, deltaFor(bin0, 1000 shares), ...);

    // Assert: bob's net token1 gain > 0 (captured LP fee)
    // Assert: alice's claimable token1 < what she would have received without bob
}
```