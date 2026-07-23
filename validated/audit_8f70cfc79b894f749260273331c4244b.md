The key finding is in `AnchoredPriceProvider.sol`. In synthetic ratio mode (`quoteFeedId != bytes32(0)`), both the base and quote feed legs are validated against the **same** `MAX_REF_STALENESS` immutable — a direct analog of M-12.

### Title
`AnchoredPriceProvider` applies a single `MAX_REF_STALENESS` to both legs of a synthetic ratio feed, enabling stale-price execution or near-constant downtime — (`smart-contracts-poc/contracts/AnchoredPriceProvider.sol`)

---

### Summary

`AnchoredPriceProvider` supports a **synthetic ratio mode** where `quoteFeedId != bytes32(0)`, computing `price(baseFeedId) / price(quoteFeedId)` (e.g. BTC/USD ÷ ETH/USD = BTC/ETH). Both legs are validated for staleness inside `_readLeg()` using the **same** immutable `MAX_REF_STALENESS`. Because different oracle feeds have different update frequencies (heartbeats), a single staleness threshold cannot correctly guard both legs simultaneously.

---

### Finding Description

The constructor accepts one `_maxRefStaleness` parameter, stored as the immutable `MAX_REF_STALENESS`:

```solidity
// AnchoredPriceProvider.sol L129-L151
constructor(
    ...
    uint256 _maxRefStaleness,
    ...
) {
    ...
    MAX_REF_STALENESS = _maxRefStaleness;
    ...
}
```

`_readLeg()` applies this single value to whichever `feedId` it is called with:

```solidity
// AnchoredPriceProvider.sol L277-L283
function _readLeg(bytes32 feedId)
    internal returns (uint256 mid, uint256 spreadBps, uint256 refTime, bool ok)
{
    (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);
    if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
    ...
}
```

In synthetic ratio mode, `_getBidAndAskPrice()` calls `_readLeg` for **both** legs with the same threshold:

```solidity
// AnchoredPriceProvider.sol L258-L271
function _getBidAndAskPrice() internal returns (uint128, uint128) {
    (uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId);   // MAX_REF_STALENESS applied
    if (!ok) return (0, type(uint128).max);

    bytes32 _quote = quoteFeedId;
    if (_quote != bytes32(0)) {
        (uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote); // same MAX_REF_STALENESS applied
        ...
    }
    ...
}
```

Real oracle feeds have different heartbeats. For example, a high-volatility asset feed (e.g. ETH/USD on Chainlink Data Streams) may update every 1 second to 1 minute, while a lower-volatility or RWA feed may update every 24 hours. The `AnchoredProviderFactory` also only validates `maxRefStaleness` against the envelope class of `baseFeedId`, not `quoteFeedId`:

```solidity
// AnchoredProviderFactory.sol L171-L180
bytes32 classId = feedClass[baseFeedId];
if (classId == bytes32(0)) classId = DEFAULT_CLASS;
Envelope storage env = envelopes[classId];
...
if (
    ...
    || maxRefStaleness < env.stalenessMin || maxRefStaleness > env.stalenessMax
    ...
) revert ParamsOutOfEnvelope();
```

The quote feed's appropriate staleness window is never checked against any envelope.

---

### Impact Explanation

Two mutually exclusive failure modes arise:

1. **Stale-price execution (bad-price execution):** If `MAX_REF_STALENESS` is set to accommodate the slower feed (e.g. 24 hours), the faster feed can be up to 24 hours stale before the check triggers. A stale fast-feed price reaches `_computeBidAsk`, producing a synthetic ratio bid/ask that does not reflect current market conditions. Traders can exploit this to extract value from the pool at the expense of LPs — a direct loss of LP principal.

2. **Near-constant downtime:** If `MAX_REF_STALENESS` is set to accommodate the faster feed (e.g. 1 minute), the slower feed will fail the staleness check on almost every swap, causing `FeedStalled` reverts. This breaks core pool swap functionality.

Both outcomes match the Metric OMM Allowed Impact Gate: bad-price execution (stale bid/ask reaches a pool swap) and broken core pool functionality causing loss of funds or unusable swap flows.

---

### Likelihood Explanation

- Synthetic ratio mode is an explicitly documented and factory-supported deployment path (`quoteFeedId` parameter in `createAnchoredProvider`).
- Different oracle feeds routinely have different heartbeats; this is not an edge case.
- No privileged action is required to trigger the bad-price path — any unprivileged swapper can execute a swap when the faster feed is stale but within the looser window.
- The `AnchoredProviderFactory` provides no mechanism to set per-leg staleness, so every synthetic ratio provider deployed through it is affected.

---

### Recommendation

Add a separate staleness parameter for the quote feed leg:

```solidity
uint256 public immutable MAX_BASE_REF_STALENESS;
uint256 public immutable MAX_QUOTE_REF_STALENESS;
```

Pass both through the constructor and `createAnchoredProvider`. In `_readLeg`, accept the applicable threshold as a parameter:

```solidity
function _readLeg(bytes32 feedId, uint256 maxStaleness)
    internal returns (uint256 mid, uint256 spreadBps, uint256 refTime, bool ok)
{
    ...
    if (_isStale(refTime, block.timestamp, maxStaleness)) return (..., false);
    ...
}
```

Call with the correct threshold per leg:

```solidity
(uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId, MAX_BASE_REF_STALENESS);
...
(uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote, MAX_QUOTE_REF_STALENESS);
```

The `AnchoredProviderFactory` envelope validation should also be extended to validate the quote feed's staleness against its own class envelope.

---

### Proof of Concept

**Setup:** Deploy `AnchoredPriceProvider` in synthetic ratio mode with:
- `baseFeedId` = ETH/USD (updates every ~1 second via Data Streams)
- `quoteFeedId` = BTC/USD (updates every ~24 hours)
- `MAX_REF_STALENESS` = 25 hours (to avoid downtime from the slow BTC feed)

**Attack:**
1. Wait until the ETH/USD feed is 2 hours stale (no new report pushed).
2. The BTC/USD feed was updated 1 hour ago — both pass the 25-hour staleness check.
3. Call `swap` on the pool. `_getBidAndAskPrice()` reads the 2-hour-old ETH/USD price and the fresh BTC/USD price, computing a synthetic BTC/ETH ratio using a stale ETH denominator.
4. If ETH moved 5% in those 2 hours, the synthetic ratio is off by ~5%, allowing the attacker to buy BTC/ETH at a price 5% below fair value, extracting that value from LPs.

The pool has no independent guard against this: `_computeBidAsk` trusts the ratio produced by `_getBidAndAskPrice` as long as both legs individually passed the shared staleness threshold. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L123-151)
```text
    constructor(
        address _factory,
        address _oracle,
        bytes32 _baseFeedId,
        bytes32 _quoteFeedId,
        uint256 _minMargin,
        uint256 _maxRefStaleness,
        uint16  _maxSpreadBps,
        bool    _mutableParams,
        int256  _marginStep,
        address _baseToken,
        address _quoteToken
    ) {
        require(_factory != address(0));
        factory = _factory;

        offchainOracle = IOffchainOracle(_oracle);
        baseFeedId = _baseFeedId;
        quoteFeedId = _quoteFeedId;

        // Tokens live ONLY here (the oracles are token-free): the pair is an explicit,
        // mandatory input — including the synthetic (two-feed) mode, where the factory
        // knows the pair when it creates the pool.
        require(_baseToken != address(0) && _quoteToken != address(0) && _baseToken != _quoteToken);
        baseToken = _baseToken;
        quoteToken = _quoteToken;

        if (_maxRefStaleness > 7 days) revert MaxRefStalenessOutOfBounds(); // 0 allowed = same-block reference
        MAX_REF_STALENESS = _maxRefStaleness;
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

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L156-194)
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
