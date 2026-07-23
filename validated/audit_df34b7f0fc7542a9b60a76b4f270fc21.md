Audit Report

## Title
Missing Deadline Check in `MetricOmmPoolLiquidityAdder` Allows Stale Liquidity Execution — (File: `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

## Summary
`MetricOmmPoolLiquidityAdder` exposes `addLiquidityExactShares` and `addLiquidityWeighted` (two overloads each) with no `deadline` parameter and no time-validity check. `MetricOmmSimpleRouter` enforces `_checkDeadline(params.deadline)` at the top of all four swap entry points, but `MetricOmmPoolLiquidityAdder` inherits only from `PeripheryPayments` — not from `MetricOmmSwapRouterBase` where `_checkDeadline` lives — so the guard is structurally absent. A miner or sequencer can delay a pending `addLiquidityWeighted` transaction until the oracle price has drifted within the same bin, causing the probe to return different `need0`/`need1` values, a lower scaling factor, and fewer LP shares issued for the same token cost.

## Finding Description

`_checkDeadline` is defined in `MetricOmmSwapRouterBase` and called in all four `MetricOmmSimpleRouter` entry points:

- `exactInputSingle` — line 68
- `exactInput` — line 93
- `exactOutputSingle` — line 131
- `exactOutput` — line 155

`MetricOmmPoolLiquidityAdder` inherits only `PeripheryPayments` and has no access to `_checkDeadline`. Its four public entry points (`addLiquidityExactShares` with owner at L56, `addLiquidityExactShares` for `msg.sender` at L71, `addLiquidityWeighted` with owner at L88, `addLiquidityWeighted` for `msg.sender` at L123) accept no `deadline` parameter and perform no time check.

The critical path is `addLiquidityWeighted`. It executes a probe call to `IMetricOmmPoolActions(pool).addLiquidity(…, abi.encode(KIND_PROBE), …)` which reads the live oracle price via `_getBidAndAskPriceX64` at execution time to determine `need0` and `need1`. The scaling factor is then `min(max0/need0, max1/need1)`, and final shares are `weight[i] * scaleWad / WAD`. If the oracle price has moved between submission and inclusion, `need0`/`need1` differ from what the user computed off-chain, and the scaling factor — and therefore shares issued — changes accordingly.

The `_validateBinAndBinPosition` guard reads `curBinIdx` and `curPosInBin` from slot0. These values are updated by pool swaps, not by external oracle feed updates. The oracle price provider (`IPriceProvider.getBidAndAskPrice()`) can return a materially different price while `curBinIdx` and `curPosInBin` remain within the user's supplied bounds, so the guard does not substitute for a deadline.

For `addLiquidityExactShares`, the user specifies exact share counts; the pool mints exactly those shares and the `maxAmountToken0`/`maxAmountToken1` caps bound the token cost. The deadline absence is a weaker concern there (no direct share loss), but the `addLiquidityWeighted` path has a concrete, quantifiable share shortfall.

## Impact Explanation

On the `addLiquidityWeighted` path, oracle price drift within the current bin causes the probe to return higher `need0` or `need1`, reducing `scaleWad`, and therefore reducing `out.shares[i]` for every bin. The user pays up to `maxAmountToken0`/`maxAmountToken1` in tokens but receives fewer LP shares than they were owed at the price they intended to transact at. This is a direct loss of owed LP assets, matching the allowed impact gate. Severity is Medium: the loss is bounded by the magnitude of intra-bin oracle drift and requires an active miner/sequencer delay, but no capital is required by the attacker and the loss is unrecoverable once the transaction is included.

## Likelihood Explanation

Any mempool-visible transaction is subject to ordering by miners/sequencers. On chains with MEV infrastructure, delaying a single transaction requires no capital and is low-cost. The `addLiquidityWeighted` path is specifically designed for users who want to add liquidity proportional to the current oracle price, making it the highest-value target: users submitting this call have the strongest expectation that execution occurs at the price they observed. The `_validateBinAndBinPosition` bounds can be set loosely (or the oracle can move within tight bounds), making the guard bypassable in practice.

## Recommendation

Add a `uint256 deadline` parameter to all four public entry points in `MetricOmmPoolLiquidityAdder` and add a `_checkDeadline` helper (mirroring the one in `MetricOmmSwapRouterBase`) or extract it to a shared base. Call it at the top of each function:

```solidity
function _checkDeadline(uint256 deadline) internal view {
    if (block.timestamp > deadline) revert TransactionExpired(deadline, block.timestamp);
}

function addLiquidityWeighted(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata weightDeltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    int8 minimalCurBin,
    uint104 minimalPosition,
    int8 maximalCurBin,
    uint104 maximalPosition,
    uint256 deadline,          // ← add
    bytes calldata extensionData
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _checkDeadline(deadline);  // ← add
    ...
}
```

Apply the same change to both `addLiquidityExactShares` overloads and the second `addLiquidityWeighted` overload.

## Proof of Concept

1. User observes oracle mid-price `P` and submits `addLiquidityWeighted(pool, owner, salt, weightDeltas, 1000e18, 500e18, minBin, minPos, maxBin, maxPos, "")`. Off-chain simulation predicts `S` shares.
2. Miner withholds the transaction.
3. External oracle feed updates: price moves to `P'` (e.g., token0 appreciates 5%) while `curBinIdx` and `curPosInBin` remain within `[minBin, maxBin]` × `[minPos, maxPos]` — `_validateBinAndBinPosition` does not revert.
4. Miner includes the transaction at `P'`. The probe call returns `need0' > need0`; `scaleWad = max0 / need0'` is smaller; `out.shares[i] = weight[i] * scaleWad / WAD` is smaller for every bin.
5. The paying `addLiquidity` call executes with the reduced share vector. User receives `S' < S` shares while paying up to the same token caps.
6. No revert occurs. The user has lost `S - S'` LP shares with no recourse.
7. Contrast: an equivalent `exactInputSingle` through `MetricOmmSimpleRouter` with a past deadline would have reverted at step 4 via `_checkDeadline`.