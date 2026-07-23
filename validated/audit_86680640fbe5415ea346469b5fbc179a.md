### Title
`ProtectedPriceProviderL2` and `PriceProviderL2` Lack Sequencer Uptime Feed Check, Allowing Swaps at Stale Prices During and After L2 Sequencer Downtime — (`smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol`, `smart-contracts-poc/contracts/PriceProviderL2.sol`)

---

### Summary

Both L2-specific price providers (`ProtectedPriceProviderL2` and `PriceProviderL2`) rely exclusively on an oracle `refTime`-based staleness check (`_isStale`) for L2 safety. Neither contract queries a Chainlink sequencer uptime feed or enforces a post-restart grace period. When the L2 sequencer goes down, oracle data freezes at the pre-downtime price. Swaps continue to execute at that frozen price for up to `MAX_TIME_DELTA` seconds (configurable up to 7 days). When the sequencer restarts, a further grace-period window exists during which the oracle data is stale but still within `MAX_TIME_DELTA`, allowing adversarial traders to drain LP funds at pre-downtime prices.

---

### Finding Description

`ProtectedPriceProviderL2._computeBidAsk` and `PriceProviderL2._getBidAndAskPrice` both call `_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)` as their sole L2 safety gate:

```solidity
// ProtectedPriceProviderL2.sol lines 206-209
if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
    return (0, type(uint128).max);
}
```

`_isStale` only checks whether the oracle's `refTime` is older than `MAX_TIME_DELTA`:

```solidity
// ProtectedPriceProviderL2.sol lines 138-153
function _isStale(uint256 refTime, uint256 nowTs, uint256 maxDelta, uint256 futureTol)
    internal pure returns (bool)
{
    if (refTime == 0) return true;
    if (refTime > nowTs) {
        return (refTime - nowTs) > futureTol;
    }
    return (nowTs - refTime) > maxDelta;
}
```

There is no call to a `sequencerUptimeFeed.latestRoundData()` and no `GRACE_PERIOD` enforcement anywhere in either contract. The constructor accepts no `_sequencerUptimeFeed` parameter:

```solidity
// ProtectedPriceProviderL2.sol lines 68-100
constructor(
    address _factory,
    address _oracle,
    bytes32 _offchainFeedId,
    int256  _marginStep,
    uint256 _maxTimeDelta,
    uint256 _futureTolerance,
    address _baseToken,
    address _quoteToken
) { ... }
```

The registry ABI (which represents the intended deployed interface) shows a contract with `sequencerUptimeFeed()` and `GRACE_PERIOD()` functions, confirming the protocol intended this check but it is absent from the actual source. The `PriceProviderFactoryL2.createPriceProvider` also passes no sequencer feed to the deployed `PriceProviderL2`.

---

### Impact Explanation

When the L2 sequencer goes down:

1. The offchain oracle (Pyth/Chainlink) stops receiving updates; `refTime` freezes at the pre-downtime value.
2. For up to `MAX_TIME_DELTA` seconds (up to 7 days by the constructor bound), `_isStale` returns `false` and `getBidAndAskPrice` returns the frozen pre-downtime bid/ask.
3. Users can still submit transactions directly through the L1 rollup contract, reaching the pool.
4. A trader who knows the real market price has moved can swap against the stale oracle price, extracting value from LPs.

When the sequencer restarts:

5. The oracle data is still stale (from before downtime) but within `MAX_TIME_DELTA`.
6. Without a grace-period check, swaps immediately resume at the stale price.
7. The attacker can front-run the first oracle update, executing at the pre-downtime price before the oracle refreshes.

The pool is a pure oracle market maker — its entire solvency guarantee rests on the oracle price being current. Stale prices directly corrupt every swap output and LP claim.

---

### Likelihood Explanation

- Arbitrum and Optimism sequencers have experienced documented downtime events.
- The protocol explicitly targets L2 deployments (Arbitrum, Base, Optimism, Avalanche, BSC, Polygon per the registry).
- No privileged action is required; any public trader can submit a swap during or immediately after sequencer downtime.
- `MAX_TIME_DELTA` is set at construction and can be up to 7 days, creating a very wide exploitation window.

---

### Recommendation

Add a sequencer uptime feed check to both `ProtectedPriceProviderL2` and `PriceProviderL2`, consistent with the Chainlink-recommended pattern and the `GRACE_PERIOD`/`sequencerUptimeFeed` interface already present in the registry ABI:

```solidity
AggregatorV3Interface public immutable sequencerUptimeFeed;
uint256 public constant GRACE_PERIOD = 3600; // 1 hour

function _isSequencerUp() internal view returns (bool) {
    (, int256 answer, uint256 startedAt,,) = sequencerUptimeFeed.latestRoundData();
    // answer == 0: sequencer is up; answer == 1: sequencer is down
    if (answer != 0) return false;
    // Enforce grace period after restart
    if (block.timestamp - startedAt < GRACE_PERIOD) return false;
    return true;
}
```

Call `_isSequencerUp()` at the top of `_computeBidAsk` / `_getBidAndAskPrice` and return the stall sentinel `(0, type(uint128).max)` if it returns `false`. Pass `_sequencerUptimeFeed` through `PriceProviderFactoryL2.createPriceProvider`.

---

### Proof of Concept

1. Deploy `ProtectedPriceProviderL2` on Arbitrum with `MAX_TIME_DELTA = 3600` (1 hour).
2. Oracle publishes price `P = 2000 USDC/ETH` at `T = 0`; `refTime = T`.
3. Arbitrum sequencer goes down at `T = 1`. Oracle stops updating; `refTime` stays at `T`.
4. Real market price drops to `P' = 1000 USDC/ETH` during downtime.
5. At `T = 1800` (30 min into downtime), attacker submits a swap via the L1 rollup contract.
6. `_isStale(T, T+1800, 3600, ...)` → `1800 <= 3600` → `false` (not stale).
7. `getBidAndAskPrice()` returns bid/ask derived from `P = 2000`.
8. Attacker buys ETH at the stale 2000 USDC price, immediately worth 1000 USDC at market — LP loses 1000 USDC per ETH traded.
9. Sequencer restarts at `T = 3000`. Attacker front-runs the first oracle update, repeating the same swap before `refTime` refreshes. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol (L68-100)
```text
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

**File:** smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol (L202-209)
```text
    /// @dev Downstream pricing: staleness, price guard, confidence spread, marginStep.
    function _computeBidAsk(uint256 price, uint256 spread, uint256 refTime)
        internal view returns (uint128, uint128)
    {
        // 1. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
            return (0, type(uint128).max);
        }
```

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L64-96)
```text
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

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L208-217)
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
```

**File:** smart-contracts-poc/contracts/PriceProviderFactoryL2.sol (L41-79)
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

        provider = address(p);
        address creator = msg.sender;

        _providers.add(provider);
        _providersByCreator[creator].add(provider);
        providerOwner[provider] = creator;

        emit ProviderDeployed(
            provider,
            creator,
            _feedId,
            _oracle,
            p.baseToken(),
            p.quoteToken(),
            _marginStep,
            _maxTimeDelta,
            _futureTolerance
        );
    }
```
