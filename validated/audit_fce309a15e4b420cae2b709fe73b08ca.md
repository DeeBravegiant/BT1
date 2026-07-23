Audit Report

## Title
Missing L2 Sequencer Uptime Check Allows Stale Pre-Outage Prices to Reach Pool Swaps — (`smart-contracts-poc/contracts/PriceProviderL2.sol`, `smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol`)

## Summary

`PriceProviderL2` and `ProtectedPriceProviderL2` are deployed on L2 (Arbitrum) and rely exclusively on a timestamp-delta staleness check (`_isStale`) to validate oracle prices. Neither contract checks whether the Arbitrum sequencer is live. When the sequencer restarts after an outage shorter than `MAX_TIME_DELTA`, the frozen pre-outage price passes the staleness check and is served to the pool as current, enabling a trader to execute swaps at a price that may be materially wrong relative to the real market.

## Finding Description

`PriceProviderL2._getBidAndAskPrice()` reads the oracle and applies only a time-delta staleness check:

```solidity
(uint256 mid, uint256 spread, , uint256 refTime) =
    IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);

if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
    return (0, type(uint128).max);
}
```

`_isStale` only checks `(nowTs - refTime) > MAX_TIME_DELTA`:

```solidity
function _isStale(uint256 refTime, uint256 nowTs, uint256 maxDelta, uint256 futureTol)
    internal pure returns (bool)
{
    if (refTime == 0) return true;
    if (refTime > nowTs) return (refTime - nowTs) > futureTol;
    return (nowTs - refTime) > maxDelta;
}
```

The oracle backends are push-based: prices are stored only when `updateReport()` is called on-chain. During a sequencer outage, no transactions land on L2, so `refTime` is frozen at the last pre-outage push. On restart:

- `refTime` = last push timestamp before outage (e.g., `T`)
- `block.timestamp` = `T + outage_duration`
- If `outage_duration < MAX_TIME_DELTA`, `_isStale` returns `false`
- The pool receives the pre-outage price as if it were current

`FUTURE_TOLERANCE` is explicitly designed for clock skew ("L2 sequencer timestamp can lag behind oracle publication time"), not sequencer downtime. No sequencer uptime feed is checked anywhere in either contract or their factory. A grep across all production `.sol` files for `sequencer`, `uptime`, `startedAt`, `GRACE_PERIOD`, and `latestRoundData` confirms zero production-code hits outside comments.

`ProtectedPriceProviderL2._computeBidAsk()` has the identical staleness-only check at line 207, with the same absence of any sequencer liveness guard.

## Impact Explanation

This is a bad-price execution: a stale pre-outage bid/ask quote reaches a `MetricOmmPool` swap. If the real market price moved during the outage (common during volatile periods that correlate with sequencer stress), the attacker receives tokens at the stale favorable price and LPs bear the loss — their token balances no longer cover LP claims at fair value. This is direct loss of LP principal, satisfying the allowed impact gate ("Bad-price execution: stale, inverted, unbounded, or unclamped bid/ask quote reaches a pool swap" and "Pool insolvency: balances fail to cover LP claims").

## Likelihood Explanation

Arbitrum sequencer outages are documented historical events. No special permissions are required: any address can call `swap()` on the pool. The exploitation window opens automatically on every sequencer restart and closes only when a fresh price is pushed by an off-chain bot — which may itself be delayed post-restart. The attacker only needs to monitor the sequencer status and submit a swap transaction in the first block after restart.

## Recommendation

Add a Chainlink L2 sequencer uptime feed check in both `PriceProviderL2` and `ProtectedPriceProviderL2`. Store the feed address as an immutable set at construction. In `_getBidAndAskPrice()` / `_computeBidAsk()`, before using the price:

```solidity
(, int256 answer, uint256 startedAt, ,) = sequencerUptimeFeed.latestRoundData();
if (answer != 0) return (0, type(uint128).max); // sequencer down
if (block.timestamp - startedAt < GRACE_PERIOD) return (0, type(uint128).max); // just restarted
```

`GRACE_PERIOD` (e.g., 3600 seconds) ensures fresh prices are pushed before the pool accepts quotes post-restart.

## Proof of Concept

1. Deploy with `MAX_TIME_DELTA = 30 minutes`. Last price push at `T`; `refTime = T`.
2. Sequencer goes down at `T + 1 min`; real ETH price drops 8% during outage.
3. Sequencer restarts at `T + 20 min`; stored `refTime` is still `T + 1 min` (last pre-outage push).
4. Attacker calls `MetricOmmPool.swap(...)` before any fresh price is pushed.
5. `_getBidAndAskPrice()` reads `refTime = T + 1 min`; `nowTs - refTime = 19 min < 30 min` → `_isStale` returns `false`.
6. Pool executes swap at pre-outage (8% inflated) ask price; attacker receives excess tokens; LPs are underpaid.

Foundry fork test: fork Arbitrum mainnet at a block just after a historical sequencer restart, set `block.timestamp` to `restartTime + 5 min`, confirm `_isStale` returns `false` with a `refTime` from before the outage, and verify `getBidAndAskPrice()` returns non-stalled values without reverting.