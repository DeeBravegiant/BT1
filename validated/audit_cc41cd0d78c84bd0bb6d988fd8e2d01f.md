Audit Report

## Title
Missing Sequencer Uptime Feed Check Allows Swaps at Frozen Oracle Prices During and After L2 Sequencer Downtime — (`smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol`, `smart-contracts-poc/contracts/PriceProviderL2.sol`)

## Summary

Both `ProtectedPriceProviderL2` and `PriceProviderL2` rely solely on `_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)` as their L2 safety gate. Neither contract queries a Chainlink sequencer uptime feed or enforces a post-restart grace period. When the L2 sequencer goes down, oracle `refTime` freezes at the pre-downtime value; if that frozen timestamp remains within `MAX_TIME_DELTA` (configurable up to 7 days), `_isStale` returns `false` and `getBidAndAskPrice` returns the stale pre-downtime bid/ask to the pool, enabling adversarial traders to extract LP funds at corrupted prices.

## Finding Description

**Root cause — `ProtectedPriceProviderL2._computeBidAsk`:**

```solidity
// ProtectedPriceProviderL2.sol L206-209
if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
    return (0, type(uint128).max);
}
```

**Root cause — `PriceProviderL2._getBidAndAskPrice`:**

```solidity
// PriceProviderL2.sol L214-217
if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
    return (0, type(uint128).max);
}
```

`_isStale` is a pure function that only compares `refTime` against `block.timestamp`:

```solidity
// PriceProviderL2.sol L135-150
function _isStale(...) internal pure returns (bool) {
    if (refTime == 0) return true;
    if (refTime > nowTs) return (refTime - nowTs) > futureTol;
    return (nowTs - refTime) > maxDelta;
}
```

There is no call to `sequencerUptimeFeed.latestRoundData()` and no `GRACE_PERIOD` enforcement anywhere in either contract. The constructors accept no `_sequencerUptimeFeed` parameter:

- `ProtectedPriceProviderL2` constructor: L68–100 — 8 parameters, no sequencer feed
- `PriceProviderL2` constructor: L64–96 — 8 parameters, no sequencer feed
- `PriceProviderFactoryL2.createPriceProvider`: L41–79 — passes no sequencer feed to `PriceProviderL2`

**Exploit flow:**

1. Oracle publishes price `P` at time `T`; `refTime = T`.
2. L2 sequencer goes down at `T+1`. Oracle stops updating; `refTime` stays at `T`.
3. Real market price moves significantly (e.g., drops 50%).
4. Attacker submits a swap directly via the L1 rollup inbox (bypassing the sequencer).
5. `_isStale(T, T+Δ, MAX_TIME_DELTA, ...)` returns `false` as long as `Δ ≤ MAX_TIME_DELTA`.
6. `getBidAndAskPrice()` returns bid/ask derived from the frozen pre-downtime price `P`.
7. Attacker buys the underpriced asset, extracting value from LPs.
8. On sequencer restart, oracle data is still stale but within `MAX_TIME_DELTA`; attacker front-runs the first oracle update, repeating the swap before `refTime` refreshes.

The registry ABI (`smart-contracts-poc/contract-registry/versions/registry.json`) confirms the protocol designed a sequencer-aware variant: `ChainlinkVerifierL2` exposes `GRACE_PERIOD()` and `sequencerUptimeFeed()`, and the `PriceProviderFactoryL2` ABI in the registry includes `_sequencerUptimeFeed` as a constructor parameter — none of which are present in the deployed source.

## Impact Explanation

This is a **bad-price execution** impact: stale, frozen bid/ask prices reach pool swaps. The pool is a pure oracle market maker — its solvency guarantee rests entirely on the oracle price being current. Every swap executed against the frozen price extracts value from LPs proportional to the price deviation. With `MAX_TIME_DELTA` up to 7 days, the exploitation window is very wide. LP principal loss is direct and unbounded by the size of the pool. This meets the Metric OMM "bad-price execution" and "direct loss of user principal / LP assets" impact criteria.

## Likelihood Explanation

- Arbitrum and Optimism sequencers have documented historical downtime events.
- The protocol explicitly targets L2 deployments (Arbitrum, Base, Optimism, Avalanche, BSC, Polygon per the registry deployments section).
- Submitting transactions via the L1 rollup inbox requires no special privileges — any public trader can do it.
- `MAX_TIME_DELTA` is set at construction and can be up to 7 days, creating a very wide exploitation window even with moderate sequencer downtime.
- No admin action is required; the vulnerability is passively present on every deployed L2 pool using these providers.

## Recommendation

Add a sequencer uptime feed check to both `ProtectedPriceProviderL2` and `PriceProviderL2`, consistent with the Chainlink-recommended pattern and the `GRACE_PERIOD`/`sequencerUptimeFeed` interface already present in the registry ABI:

```solidity
AggregatorV3Interface public immutable sequencerUptimeFeed;
uint256 public constant GRACE_PERIOD = 3600; // 1 hour

function _isSequencerUp() internal view returns (bool) {
    (, int256 answer, uint256 startedAt,,) = sequencerUptimeFeed.latestRoundData();
    if (answer != 0) return false;
    if (block.timestamp - startedAt < GRACE_PERIOD) return false;
    return true;
}
```

Call `_isSequencerUp()` at the top of `_computeBidAsk` / `_getBidAndAskPrice` and return `(0, type(uint128).max)` if it returns `false`. Pass `_sequencerUptimeFeed` through `PriceProviderFactoryL2.createPriceProvider` to the deployed provider.

## Proof of Concept

1. Fork Arbitrum mainnet; deploy `PriceProviderL2` with `MAX_TIME_DELTA = 3600`.
2. Push oracle price `P = 2000e8` at `T = 0`; `refTime = T`.
3. `vm.warp(T + 1800)` (simulate 30 min of sequencer downtime; oracle not updated).
4. Call `getBidAndAskPrice()` — `_isStale(T, T+1800, 3600, ...)` returns `false`; bid/ask derived from `P = 2000` is returned.
5. Execute a swap buying ETH at the stale 2000 USDC price while real market price is 1000 USDC.
6. Verify LP balance decreases by ~1000 USDC per ETH traded.
7. `vm.warp(T + 3001)` (sequencer restarts); call `getBidAndAskPrice()` again before oracle update — same stale price returned, same exploit repeatable.