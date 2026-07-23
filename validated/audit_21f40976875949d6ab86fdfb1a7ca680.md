Audit Report

## Title
Missing Sequencer Uptime / Grace-Period Check Allows Stale-Price Swap Execution After Sequencer Restart - (`smart-contracts-poc/contracts/PriceProviderL2.sol` and `smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol`)

## Summary
`PriceProviderL2` and `ProtectedPriceProviderL2` perform only a time-delta staleness check (`_isStale`) before serving bid/ask quotes to `MetricOmmPool` swaps. Neither contract checks a Chainlink sequencer uptime feed or enforces a post-restart grace period. When the L2 sequencer goes offline and restarts, the last stored oracle report — whose `refTime` predates the outage — can still pass the staleness check and be served as a live quote, enabling a trader to execute swaps against economically stale prices at LP expense.

## Finding Description
Both providers share the same staleness guard in `_getBidAndAskPrice()` / `_computeBidAsk()`:

```solidity
// PriceProviderL2.sol L208-217
function _getBidAndAskPrice() internal returns (uint128, uint128) {
    (uint256 mid, uint256 spread, , uint256 refTime) =
        IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);
    if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
        return (0, type(uint128).max);
    }
    ...
}
```

`_isStale` only checks the age of `refTime` against `MAX_TIME_DELTA` (bounded to `(0, 7 days]`):

```solidity
// PriceProviderL2.sol L135-150
function _isStale(uint256 refTime, uint256 nowTs, uint256 maxDelta, uint256 futureTol)
    internal pure returns (bool) {
    if (refTime == 0) return true;
    if (refTime > nowTs) return (refTime - nowTs) > futureTol;
    return (nowTs - refTime) > maxDelta;
}
```

A grep for `latestRoundData`, `sequencerUptimeFeed`, `GRACE_PERIOD`, and `_checkSequencer` across all `.sol` files returns **zero matches**. Neither provider calls a sequencer uptime feed before consuming oracle data. The oracle layer (`ChainlinkOracle.updateReport`) stores DON-signed reports pushed by off-chain keepers; when the sequencer is down, no new reports can be pushed. When the sequencer restarts, the last stored report's `refTime` (a non-zero pre-outage timestamp) may be, e.g., 20 minutes old. With `MAX_TIME_DELTA = 1 hour`, `_isStale` returns `false` and the stale price is served to the pool. Notably, the registry ABI for `PriceProviderL2` (version in `registry.json`) exposes `sequencerUptimeFeed()` and `GRACE_PERIOD()` as public functions and accepts `_sequencerUptimeFeed` as a constructor parameter — confirming the check was planned but is absent from the current source.

## Impact Explanation
A swap on `MetricOmmPool` calls `getBidAndAskPrice()` on the provider. If the provider returns a pre-outage price that is, e.g., 5% below the current market (because the market moved during sequencer downtime), a trader can execute a `zeroForOne` swap at the stale low ask, receiving token1 at a price 5% cheaper than the real market. LPs bear the loss: they deliver token1 at the stale price and receive token0 at the stale price, with no recourse. This is a direct, quantifiable loss of LP principal proportional to the price movement during the outage and the swap size. It satisfies the "bad-price execution: stale bid/ask quote reaches a pool swap" criterion.

## Likelihood Explanation
L2 sequencer outages are documented historical events on Arbitrum, Optimism, and Base. The attack requires no privileged access: any public trader can call `swap` immediately after the sequencer restarts, before fresh oracle data is pushed by keepers. The attacker can front-run the keeper update in the first block after restart. The window is bounded by keeper latency (seconds to minutes), but the attacker's transaction can be included in the first post-restart block.

## Recommendation
Add a sequencer uptime check to both `PriceProviderL2._getBidAndAskPrice()` and `ProtectedPriceProviderL2._getBidAndAskPrice()` (or a shared base), consistent with the ABI already registered in `registry.json`:

```solidity
AggregatorV3Interface public immutable sequencerUptimeFeed;
uint256 public constant GRACE_PERIOD = 3600;

function _checkSequencer() internal view {
    if (address(sequencerUptimeFeed) == address(0)) return;
    (, int256 answer, uint256 startedAt,,) = sequencerUptimeFeed.latestRoundData();
    if (answer != 0) revert SequencerDown();
    if (block.timestamp - startedAt < GRACE_PERIOD) revert GracePeriodNotOver();
}
```

Call `_checkSequencer()` at the top of `_getBidAndAskPrice()` before reading oracle data, returning the `(0, type(uint128).max)` sentinel (which propagates to `FeedStalled`) if the check fails.

## Proof of Concept
1. Deploy `PriceProviderL2` on Arbitrum with `MAX_TIME_DELTA = 1 hours`.
2. Push a fresh oracle report at `t = T` (price = 1000 USDC/ETH), so `refTime = T`.
3. Simulate sequencer downtime from `t = T` to `t = T + 1800` (30 min); no new reports can be pushed; real market moves to 1050 USDC/ETH.
4. At `t = T + 1800` the sequencer restarts. The stored report has `refTime = T`, `nowTs = T + 1800`. `_isStale(T, T+1800, 3600, futureTol)` → `(1800) <= 3600` → **not stale**. The stale price 1000 is served.
5. Attacker calls `MetricOmmPool.swap` (zeroForOne, exactIn) and buys ETH at 1000 USDC instead of 1050, extracting 50 USDC per ETH from LP reserves.
6. A keeper pushes the fresh report seconds later, but the attacker's transaction is already included in the first post-restart block.