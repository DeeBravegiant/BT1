### Title
L2 Price Providers Lack Sequencer Uptime Check, Enabling Stale-Price Swaps During Sequencer Downtime — (`smart-contracts-poc/contracts/PriceProviderL2.sol`, `smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol`)

---

### Summary

`PriceProviderL2` and `ProtectedPriceProviderL2` are explicitly deployed on L2 networks but contain no Chainlink L2 Sequencer Uptime Feed check. Their only liveness guard is a `refTime`-based staleness check. When the L2 sequencer goes offline, no new oracle data can be pushed on-chain, yet the stored data's `refTime` may remain within `MAX_TIME_DELTA` for an extended window. During that window the pool continues to quote and execute swaps at the pre-downtime price, which may diverge materially from the true market price.

---

### Finding Description

Both L2 price providers implement `_isStale` to reject oracle data older than `MAX_TIME_DELTA` (configurable up to 7 days):

```solidity
// PriceProviderL2.sol lines 135-150
function _isStale(
    uint256 refTime, uint256 nowTs,
    uint256 maxDelta, uint256 futureTol
) internal pure returns (bool) {
    if (refTime == 0) return true;
    if (refTime > nowTs) return (refTime - nowTs) > futureTol;
    return (nowTs - refTime) > maxDelta;
}
``` [1](#0-0) 

This check is applied in `_getBidAndAskPrice` before computing bid/ask: [2](#0-1) 

The identical pattern appears in `ProtectedPriceProviderL2._computeBidAsk`: [3](#0-2) 

Neither contract stores or queries a `sequencerUptimeFeed`. The factory that deploys `PriceProviderL2` accepts no such parameter: [4](#0-3) 

**Attack scenario:**

1. The L2 sequencer is running. A keeper pushes oracle data at time `T`; `refTime = T`.
2. The sequencer goes offline at `T + ε`. No further data can be pushed.
3. The real market price moves significantly (e.g., a sharp drop).
4. A user calls `swap()` on the pool at time `T + Δ` where `Δ < MAX_TIME_DELTA`.
5. `_isStale` returns `false` because `(T + Δ) − T = Δ < MAX_TIME_DELTA`.
6. The pool quotes and executes the swap at the pre-downtime price.
7. The user receives tokens at a price that does not reflect the current market, draining value from LPs.

The vulnerability window is `MAX_TIME_DELTA − ε`, which can be up to 7 days by the constructor bound: [5](#0-4) 

The protocol's own documentation claims "sequencer-down checks for L2s" exist as a safety guard, but no such check is present in the deployed source.

---

### Impact Explanation

When the L2 sequencer is down, the pool continues to execute swaps at a stale oracle price. This is a **bad-price execution** impact: the bid/ask delivered to the pool's swap math is stale and does not reflect the true market. LPs bear the resulting loss because the pool's reserves are depleted at incorrect prices. This matches the allowed impact gate: *"Bad-price execution: stale, inverted, unbounded, or unclamped bid/ask quote reaches a pool swap."*

---

### Likelihood Explanation

L2 sequencer outages are a documented, recurring event on Arbitrum, Optimism, and Base. The Chainlink L2 Sequencer Uptime Feed exists precisely because this scenario is considered a realistic operational risk. The vulnerability requires no privileged access and no attacker action beyond submitting a swap transaction during the downtime window.

---

### Recommendation

Add an immutable `AggregatorV3Interface sequencerUptimeFeed` to both `PriceProviderL2` and `ProtectedPriceProviderL2`. At the top of `_getBidAndAskPrice` / `_computeBidAsk`, query the feed and revert (or return the stalled sentinel `(0, type(uint128).max)`) if the sequencer is reported down or if the grace period since recovery has not elapsed:

```solidity
// Example guard to add at the start of _getBidAndAskPrice
if (address(sequencerUptimeFeed) != address(0)) {
    (, int256 answer, uint256 startedAt, ,) =
        sequencerUptimeFeed.latestRoundData();
    // answer == 1 means sequencer is down
    if (answer != 0 || block.timestamp - startedAt < GRACE_PERIOD) {
        return (0, type(uint128).max); // fail closed
    }
}
```

Expose `sequencerUptimeFeed` as a constructor parameter in `PriceProviderFactoryL2.createPriceProvider` and pass it through. On L1 deployments, pass `address(0)` to skip the check.

---

### Proof of Concept

```solidity
// Scenario: sequencer goes down, stale price accepted
// Setup: MAX_TIME_DELTA = 1 hours, oracle last pushed at T=1000
// Sequencer goes down at T=1001
// At T=1060 (59 minutes later), price has dropped 20%

// _isStale(refTime=1000, nowTs=1060, maxDelta=3600, futureTol=X)
// → (1060 - 1000) = 60 < 3600 → NOT stale → pool quotes pre-drop price

// Attacker swaps token1 for token0 at the inflated pre-drop bid price
// Pool pays out more token0 than the current market warrants
// LPs suffer the loss equivalent to the 20% price gap × swap size
``` [6](#0-5) [7](#0-6)

### Citations

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L92-95)
```text
        if (_maxTimeDelta == 0 || _maxTimeDelta > 7 days) revert MaxTimeDeltaOutOfBounds();
        if (_futureTolerance > 1 hours) revert FutureToleranceOutOfBounds();
        MAX_TIME_DELTA   = _maxTimeDelta;
        FUTURE_TOLERANCE = _futureTolerance;
```

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L135-150)
```text
    function _isStale(
        uint256 refTime,
        uint256 nowTs,
        uint256 maxDelta,
        uint256 futureTol
    ) internal pure returns (bool) {
        if (refTime == 0) return true;

        if (refTime > nowTs) {
            // refTime in the future: tolerate only within futureTol
            return (refTime - nowTs) > futureTol;
        }

        // refTime in the past or equal: check age
        return (nowTs - refTime) > maxDelta;
    }
```

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L208-248)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        // 1. Read via the unified price(feedId, pool) path, forwarding the pool (msg.sender).
        //    refTime is already in seconds.
        (uint256 mid, uint256 spread, , uint256 refTime) =
            IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);

        // 2. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
            return (0, type(uint128).max);
        }

        // 3. Basic validity — price must be positive, spread must not be stalled marker
        if (mid == 0 || spread >= ORACLE_BPS) {
            return (0, type(uint128).max);
        }

        // 4. Price guard check (moved from oracle)
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(offchainFeedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) {
            return (0, type(uint128).max);
        }

        // 5. Compute bid/ask from mid + confidence-adjusted spread
        //    confidenceParam multiplies oracle spread; 0 means no spread
        uint256 adjustedSpread = spread * confidenceParam;
        (uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);

        // 6. Apply marginStep adjustment
        (uint256 bidOut, bool bidOk) = _applyBidAdjustments(bid);
        if (!bidOk || bidOut > type(uint128).max) return (0, type(uint128).max);

        (uint256 askOut, bool askOk) = _applyAskAdjustments(ask);
        if (!askOk || askOut > type(uint128).max) return (0, type(uint128).max);

        // 7. Hard invariant: bid must be strictly less than ask.
        //    Can be violated when marginStep < 0 and confidence is too small.
        if (bidOut >= askOut) return (0, type(uint128).max);

        return (uint128(bidOut), uint128(askOut));
    }
```

**File:** smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol (L196-238)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        (uint256 mid, uint256 spread, , uint256 refTime) =
            IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);
        return _computeBidAsk(mid, spread, refTime);
    }

    /// @dev Downstream pricing: staleness, price guard, confidence spread, marginStep.
    function _computeBidAsk(uint256 price, uint256 spread, uint256 refTime)
        internal view returns (uint128, uint128)
    {
        // 1. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
            return (0, type(uint128).max);
        }

        // 2. Basic validity — price must be positive, spread must not be stalled marker
        if (price == 0 || spread >= ORACLE_BPS) {
            return (0, type(uint128).max);
        }

        // 3. Price guard check
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(offchainFeedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (price < guardMin || price > guardMax) {
            return (0, type(uint128).max);
        }

        // 4. Compute bid/ask from mid + confidence-adjusted spread
        uint256 adjustedSpread = spread * confidenceParam;
        (uint256 bid, uint256 ask) = _getBidAskFrom(price, adjustedSpread);

        // 5. Apply marginStep adjustment
        (uint256 bidOut, bool bidOk) = _applyBidAdjustments(bid);
        if (!bidOk || bidOut > type(uint128).max) return (0, type(uint128).max);

        (uint256 askOut, bool askOk) = _applyAskAdjustments(ask);
        if (!askOk || askOut > type(uint128).max) return (0, type(uint128).max);

        // 6. Hard invariant: bid must be strictly less than ask.
        if (bidOut >= askOut) return (0, type(uint128).max);

        return (uint128(bidOut), uint128(askOut));
    }
```

**File:** smart-contracts-poc/contracts/PriceProviderFactoryL2.sol (L41-59)
```text
    function createPriceProvider(
        address _oracle,
        bytes32 _feedId,
        int256  _marginStep,
        uint256 _maxTimeDelta,
        uint256 _futureTolerance,
        address _baseToken,
        address _quoteToken
    ) external override returns (address provider) {
        PriceProviderL2 p = new PriceProviderL2(
            address(this),
            _oracle,
            _feedId,
            _marginStep,
            _maxTimeDelta,
            _futureTolerance,
            _baseToken,
            _quoteToken
        );
```
