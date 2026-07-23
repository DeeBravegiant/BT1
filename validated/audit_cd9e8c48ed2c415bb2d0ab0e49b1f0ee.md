### Title
Single `MAX_REF_STALENESS` Applied to Both Legs of Synthetic Ratio Pairs Allows Stale Quote-Feed Price to Reach Pool Swaps — (File: `smart-contracts-poc/contracts/AnchoredPriceProvider.sol`)

---

### Summary

`AnchoredPriceProvider` supports synthetic ratio pricing (e.g. BTC/ETH = BTC/USD ÷ ETH/USD). Both the base and quote feed legs are validated against the **same** immutable `MAX_REF_STALENESS`. The `AnchoredProviderFactory` validates this threshold only against the **base feed's** class envelope — the quote feed's heartbeat is never checked. If the quote feed has a shorter heartbeat than the base feed, the staleness guard is too lenient for the quote leg, allowing a stale price to pass and reach pool swaps.

---

### Finding Description

In `_readLeg()`, both `baseFeedId` and `quoteFeedId` are checked against the same `MAX_REF_STALENESS` immutable:

```solidity
// AnchoredPriceProvider.sol _readLeg()
if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
```

`_getBidAndAskPrice()` calls `_readLeg` for both legs:

```solidity
(uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId);
...
bytes32 _quote = quoteFeedId;
if (_quote != bytes32(0)) {
    (uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote);  // same MAX_REF_STALENESS
    ...
    mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
``` [1](#0-0) 

`MAX_REF_STALENESS` is a single immutable set at construction: [2](#0-1) 

In `AnchoredProviderFactory.createAnchoredProvider()`, the staleness parameter is validated **only** against the `baseFeedId` class envelope — the `quoteFeedId` is never checked against any envelope:

```solidity
bytes32 classId = feedClass[baseFeedId];   // ← only base feed
if (classId == bytes32(0)) classId = DEFAULT_CLASS;
Envelope storage env = envelopes[classId];
if (
    ...
    || maxRefStaleness < env.stalenessMin || maxRefStaleness > env.stalenessMax
    ...
) revert ParamsOutOfEnvelope();
``` [3](#0-2) 

There is no `MAX_QUOTE_REF_STALENESS` parameter, no per-leg staleness immutable, and no factory-side envelope check for the quote feed's heartbeat.

---

### Impact Explanation

For a synthetic pair such as BTC/ETH (= BTC/USD ÷ ETH/USD):

- BTC/USD may have a 24-hour heartbeat → `MAX_REF_STALENESS = 86400 s` is appropriate for the base leg.
- ETH/USD has a 1-hour heartbeat → the same `MAX_REF_STALENESS = 86400 s` is far too lenient for the quote leg.

If the ETH/USD feed goes stale (e.g. 12 hours without an update), `_isStale(refTime, block.timestamp, 86400)` returns `false` — the stale ETH price passes. The ratio `mid = BTC_USD / ETH_USD_stale` is computed with a price that may be significantly off. The resulting bid/ask is anchored to a wrong mid, and the pool executes swaps at that bad price. Traders can extract value from LPs by swapping against the mispriced pool.

This is a **bad-price execution** impact: a stale, unclamped quote-leg price reaches a live pool swap.

---

### Likelihood Explanation

- Synthetic ratio mode (`quoteFeedId != 0`) is an explicitly supported and documented feature of `AnchoredPriceProvider`.
- The factory enforces no staleness envelope for the quote feed, so a creator can legally deploy a provider with `MAX_REF_STALENESS` calibrated to the base feed's heartbeat while the quote feed has a much shorter one.
- Feed staleness events (keeper outages, network congestion) are realistic operational scenarios.
- No additional privilege is required beyond deploying a provider through the factory (permissionless call).

---

### Recommendation

Add a separate `maxQuoteRefStaleness` constructor parameter and immutable for the quote feed leg. In `AnchoredProviderFactory.createAnchoredProvider()`, look up the quote feed's class envelope and validate `maxQuoteRefStaleness` against it independently. Pass the appropriate threshold to `_readLeg` for each leg:

```solidity
// In _getBidAndAskPrice():
(uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId, MAX_REF_STALENESS);
if (_quote != bytes32(0)) {
    (uint256 mid2, ..., bool ok2) = _readLeg(_quote, MAX_QUOTE_REF_STALENESS);
    ...
}
```

---

### Proof of Concept

1. Admin sets envelope for class `BTC` with `stalenessMax = 86400` (24 h).
2. Creator calls `createAnchoredProvider(oracle, BTC_USD_FEED, ETH_USD_FEED, ..., maxRefStaleness=86400, ...)`.
   - Factory checks `86400 <= env.stalenessMax` for `BTC_USD_FEED` → passes.
   - No check is performed for `ETH_USD_FEED` (1-hour heartbeat).
3. ETH/USD oracle is not updated for 12 hours (keeper outage).
4. A trader calls `pool.swap(...)`. The pool calls `getBidAndAskPrice()`.
5. `_readLeg(ETH_USD_FEED)` runs `_isStale(refTime=now-43200, now, 86400)` → `43200 < 86400` → **not stale**, returns stale ETH price.
6. `mid = BTC_USD_fresh / ETH_USD_stale` — ratio is wrong by the ETH price drift over 12 hours.
7. Pool quotes bid/ask anchored to the wrong mid; trader swaps at the mispriced rate, extracting value from LPs. [4](#0-3) [5](#0-4)

### Citations

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L75-80)
```text
    /// @notice Reference older than this (seconds) halts quoting — never clamp to a stale anchor.
    ///         Zero means the reference must be in the current block (refTime == block.timestamp).
    uint256 public immutable MAX_REF_STALENESS;
    /// @notice Circuit breaker: reference uncertainty above this (bps) means the feed is broken — halt.
    ///         Below it, growing `spreadBps` only widens the band (widen, don't halt).
    uint16  public immutable MAX_SPREAD_BPS;
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L258-272)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        (uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId);
        if (!ok) return (0, type(uint128).max);

        bytes32 _quote = quoteFeedId;
        if (_quote != bytes32(0)) {
            (uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote);
            if (!ok2 || mid2 == 0) return (0, type(uint128).max);
            // Synthetic ratio (8-decimal): mid1 / mid2. Relative uncertainties of a ratio add.
            mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
            spreadBps += spreadBps2;
        }

        return _computeBidAsk(mid, spreadBps);
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L277-295)
```text
    function _readLeg(bytes32 feedId)
        internal returns (uint256 mid, uint256 spreadBps, uint256 refTime, bool ok)
    {
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);

        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);

        // Basic validity — mid positive, spreadBps not the stalled/off-hours marker (the Chainlink oracle
        // writes spreadBps = ORACLE_BPS when an RWA market is closed).
        if (mid == 0 || spreadBps >= ORACLE_BPS) return (mid, spreadBps, refTime, false);

        // Per-leg price guard.
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(feedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) return (mid, spreadBps, refTime, false);

        ok = true;
    }
```

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L156-195)
```text
    function createAnchoredProvider(
        address oracle,
        bytes32 baseFeedId,
        bytes32 quoteFeedId,
        uint256 minMargin,
        uint256 maxRefStaleness,
        uint16  maxSpreadBps,
        bool    mutableParams,
        int256  marginStep,
        address baseToken,
        address quoteToken
    ) external override returns (address provider) {
        if (!_oracles.contains(oracle)) revert OracleNotAllowed(oracle);

        // Feeds without an explicit class fall back to the admin-configured DEFAULT_CLASS envelope.
        bytes32 classId = feedClass[baseFeedId];
        if (classId == bytes32(0)) classId = DEFAULT_CLASS;

        Envelope storage env = envelopes[classId];
        if (!env.exists) revert EnvelopeNotFound(classId);
        if (
            minMargin < env.minMarginMin || minMargin > env.minMarginMax
            || maxRefStaleness < env.stalenessMin || maxRefStaleness > env.stalenessMax
            || maxSpreadBps < env.maxSpreadMin || maxSpreadBps > env.maxSpreadMax
        ) revert ParamsOutOfEnvelope();

        AnchoredPriceProvider p = new AnchoredPriceProvider(
            address(this),
            oracle,
            baseFeedId,
            quoteFeedId,
            minMargin,
            maxRefStaleness,
            maxSpreadBps,
            mutableParams,
            marginStep,
            baseToken,
            quoteToken
        );

```
