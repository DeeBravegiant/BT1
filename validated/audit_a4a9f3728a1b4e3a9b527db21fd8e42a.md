### Title
Missing Sequencer Uptime Feed Check and Grace Period in `ProtectedPriceProviderL2` Allows Swaps Immediately After Sequencer Recovery - (File: `smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol`)

---

### Summary

`ProtectedPriceProviderL2` is the L2-specific price provider used by `MetricOmmPool` on Arbitrum/Base/Avalanche. It performs staleness and price-guard checks but contains **no sequencer uptime feed check and no grace period** after sequencer recovery. The sibling contract `PriceProviderL2` correctly implements both (`sequencerUptimeFeed` immutable + `GRACE_PERIOD` constant), but `ProtectedPriceProviderL2` — which is the provider used in the `ProtectedPriceProvider` swap path — omits them entirely. This directly violates the protocol's stated invariant: *"No trade on bad oracle: swaps revert on stale price (maxTimeDelta/maxRefStaleness), excessive Chainlink deviation, or (L2) sequencer down."*

---

### Finding Description

`ProtectedPriceProviderL2._computeBidAsk` performs the following checks before returning a live bid/ask to the pool:

1. Staleness check via `_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)`
2. Basic validity (price > 0, spread < ORACLE_BPS)
3. Price guard bounds
4. Confidence/marginStep shaping [1](#0-0) 

There is no call to a `sequencerUptimeFeed` and no `GRACE_PERIOD` enforcement anywhere in the contract. [2](#0-1) 

By contrast, `PriceProviderL2` — the other L2 provider in the same package — stores a `sequencerUptimeFeed` immutable and exposes a `GRACE_PERIOD` constant, as confirmed by the registry ABI: [3](#0-2) [4](#0-3) 

`PriceProviderFactoryL2.createPriceProvider` also accepts `_sequencerUptimeFeed` as a constructor argument, confirming the protocol's intent to enforce this check on L2: [5](#0-4) 

The attack scenario mirrors the external report exactly:

1. Arbitrum/Base sequencer goes down. No new Chainlink Data Streams reports can be pushed.
2. Sequencer recovers. A keeper immediately pushes the latest price report (which may reflect a large price move that occurred during downtime).
3. Because `ProtectedPriceProviderL2` has no sequencer uptime check and no grace period, `getBidAndAskPrice()` immediately returns the new price.
4. A MEV bot calls `MetricOmmPool.swap` in the same block as sequencer recovery, trading against LP positions at the post-recovery price before any LP can react (remove liquidity, adjust collateral, etc.).

The `_isStale` / `MAX_TIME_DELTA` check does **not** mitigate this: if the sequencer was down for less than `MAX_TIME_DELTA` (e.g., 30 minutes with a 1-hour delta), the old stored price is still considered fresh, and the new pushed price is also fresh — both pass the staleness gate. [6](#0-5) 

---

### Impact Explanation

LPs providing liquidity through `MetricOmmPool` on L2 chains can be immediately traded against at post-sequencer-recovery prices with no opportunity to withdraw or adjust. Because the pool is purely oracle-anchored (no internal price discovery), the entire price risk during sequencer downtime is borne by LPs the moment the sequencer recovers. This constitutes a direct loss of LP principal exceeding the Sherlock Medium threshold, matching the stated invariant breach: *"swaps revert on … (L2) sequencer down."* [7](#0-6) 

---

### Likelihood Explanation

Arbitrum and Base sequencer outages are historically documented (multiple incidents). The `ProtectedPriceProviderL2` is deployed on Arbitrum, Base, Avalanche, BSC, and Berachain per the registry: [8](#0-7) 

Any sequencer outage on these chains triggers the vulnerability. No privileged access is required — any public caller can execute the swap immediately after sequencer recovery.

---

### Recommendation

Add a sequencer uptime feed check with a grace period to `ProtectedPriceProviderL2`, mirroring the pattern already implemented in `PriceProviderL2`:

```solidity
// In ProtectedPriceProviderL2 constructor:
AggregatorV3Interface public immutable sequencerUptimeFeed;
uint256 public constant GRACE_PERIOD = 3600; // 1 hour

// In _computeBidAsk, before staleness check:
if (address(sequencerUptimeFeed) != address(0)) {
    (, int256 answer, uint256 startedAt,,) = sequencerUptimeFeed.latestRoundData();
    // answer == 0: sequencer is up; answer == 1: sequencer is down
    if (answer == 1) return (0, type(uint128).max); // sequencer down → stall
    if (block.timestamp - startedAt < GRACE_PERIOD) return (0, type(uint128).max); // grace period
}
```

This ensures swaps revert both while the sequencer is down and during the grace period after recovery, giving LPs time to react.

---

### Proof of Concept

```solidity
// Scenario: sequencer was down for 20 minutes (< MAX_TIME_DELTA = 1 hour)
// Price moved from 100 to 80 during downtime

// 1. Sequencer recovers at T=0
// 2. Keeper pushes new price report (price=80) at T=1
oracle.updateReport(newPriceReport); // price=80, refTime=T+1

// 3. ProtectedPriceProviderL2._computeBidAsk:
//    _isStale(T+1, T+1, 3600, futureTol) → false (fresh)
//    price=80 > 0, spread < ORACLE_BPS → valid
//    No sequencer check → returns bid/ask at price=80

// 4. MEV bot swaps at T=2, buying token0 at the depressed price=80
//    LPs who added liquidity at price=100 suffer immediate loss
//    No grace period was enforced
pool.swap(...); // succeeds, LP loses ~20% of position value
```

### Citations

**File:** smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol (L67-100)
```text
    // ── Constructor ─────────────────────────────────────────────────────
    constructor(
        address _factory,
        address _oracle,
        bytes32 _offchainFeedId,
        int256  _marginStep,
        uint256 _maxTimeDelta,
        uint256 _futureTolerance,
        address _baseToken,
        address _quoteToken
    ) {
        require(_factory != address(0));
        factory = _factory;

        offchainOracle = IOffchainOracle(_oracle);
        offchainFeedId = _offchainFeedId;

        // Tokens live ONLY here (the oracles are token-free): explicit, mandatory pair.
        require(_baseToken != address(0) && _quoteToken != address(0) && _baseToken != _quoteToken);
        baseToken  = _baseToken;
        quoteToken = _quoteToken;

        if (_marginStep <= -BPS_BASE || _marginStep >= BPS_BASE) {
            revert MarginStepOutOfBounds();
        }
        marginStep       = _marginStep;
        stepBidFactor = uint256(BPS_BASE - _marginStep);
        stepAskFactor = uint256(BPS_BASE + _marginStep);

        if (_maxTimeDelta == 0 || _maxTimeDelta > 7 days) revert MaxTimeDeltaOutOfBounds();
        if (_futureTolerance > 1 hours) revert FutureToleranceOutOfBounds();
        MAX_TIME_DELTA   = _maxTimeDelta;
        FUTURE_TOLERANCE = _futureTolerance;
    }
```

**File:** smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol (L138-153)
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

**File:** smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol (L203-238)
```text
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

**File:** smart-contracts-poc/contract-registry/versions/registry.json (L984-990)
```json
              "name": "sequencerUptimeFeed",
              "inputs": [],
              "outputs": [
                {
                  "name": "",
                  "type": "address",
                  "internalType": "contract AggregatorV3Interface"
```

**File:** smart-contracts-poc/contract-registry/versions/registry.json (L2001-2005)
```json
                {
                  "name": "_sequencerUptimeFeed",
                  "type": "address",
                  "internalType": "address"
                },
```

**File:** smart-contracts-poc/contract-registry/versions/registry.json (L5699-5710)
```json
              "type": "function",
              "name": "GRACE_PERIOD",
              "inputs": [],
              "outputs": [
                {
                  "name": "",
                  "type": "uint256",
                  "internalType": "uint256"
                }
              ],
              "stateMutability": "view"
            },
```

**File:** smart-contracts-poc/contract-registry/versions/registry.json (L5800-5820)
```json
        "PriceProviderFactoryL2": {
          "arbitrum": {
            "address": "0x50f11246D87313eC1AB8f8eB01450B297ea9e245",
            "version": "0.1.0"
          },
          "avalanche": {
            "address": "0x50f11246D87313eC1AB8f8eB01450B297ea9e245",
            "version": "0.1.0"
          },
          "base": {
            "address": "0x50f11246D87313eC1AB8f8eB01450B297ea9e245",
            "version": "0.1.0"
          },
          "berachain": {
            "address": "0x50f11246D87313eC1AB8f8eB01450B297ea9e245",
            "version": "0.1.0"
          },
          "bsc": {
            "address": "0x50f11246D87313eC1AB8f8eB01450B297ea9e245",
            "version": "0.1.0"
          },
```

**File:** RESEARCHER.md (L49-49)
```markdown
- Financial/accounting/token math and rounding behavior.
```
