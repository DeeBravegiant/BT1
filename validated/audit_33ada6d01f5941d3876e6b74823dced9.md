### Title
`AnchoredPriceProvider` L1-Only Staleness Check Permanently Breaks Swaps on L2 Deployments — (`smart-contracts-poc/contracts/AnchoredPriceProvider.sol`)

---

### Summary

`AnchoredPriceProvider`, described as "the one standard provider for public pools," uses an L1-only staleness check that unconditionally rejects any oracle `refTime` ahead of `block.timestamp`. On L2 chains (Base, HyperEVM), where the sequencer's `block.timestamp` routinely lags behind real-world oracle publication time, this causes every call to `getBidAndAskPrice()` to revert with `FeedStalled`, making all swaps on pools using this provider permanently unusable.

---

### Finding Description

The protocol explicitly acknowledges L2 clock-skew by shipping `PriceProviderL2` and `ProtectedPriceProviderL2`, both of which accept a `FUTURE_TOLERANCE` constructor parameter that allows oracle `refTime` to be up to 1 hour ahead of `block.timestamp`:

```solidity
// PriceProviderL2._isStale — L2-aware
function _isStale(uint256 refTime, uint256 nowTs, uint256 maxDelta, uint256 futureTol)
    internal pure returns (bool) {
    if (refTime == 0) return true;
    if (refTime > nowTs) {
        return (refTime - nowTs) > futureTol;   // tolerates clock skew
    }
    return (nowTs - refTime) > maxDelta;
}
```

`AnchoredPriceProvider`, however, uses the L1 variant with no `futureTol` parameter:

```solidity
// AnchoredPriceProvider._isStale — L1-only
function _isStale(uint256 refTime, uint256 nowTs, uint256 maxDelta)
    internal pure returns (bool) {
    if (refTime == 0) return true;
    if (refTime > nowTs) return true;   // hard-rejects any future timestamp
    return (nowTs - refTime) > maxDelta;
}
```

This check is called inside `_readLeg`, which is the only path through which `_getBidAndAskPrice` reads oracle data:

```solidity
function _readLeg(bytes32 feedId) internal returns (...) {
    (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);
    if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
    ...
}
```

When `ok == false`, `_getBidAndAskPrice` returns the sentinel `(0, type(uint128).max)`, and `getBidAndAskPrice` reverts with `FeedStalled`. There is no `AnchoredPriceProviderL2` variant in the repository.

---

### Impact Explanation

Every swap on a pool whose `priceProvider` is an `AnchoredPriceProvider` instance deployed on an L2 chain will revert at `MetricOmmPool._getBidAndAskPriceX64()` → `PriceProviderFailed(reason)` whenever the oracle's `refTime` exceeds `block.timestamp` by even 1 second. Because L2 sequencer timestamps routinely lag real-world time, this condition is not edge-case — it is the normal operating state. The result is a complete, persistent DoS on all swap flows for affected pools, matching the allowed impact gate: *"Broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows."*

---

### Likelihood Explanation

High. The protocol targets Base and HyperEVM (both L2s). The existence of `PriceProviderL2` and `ProtectedPriceProviderL2` proves the team is aware of L2 clock-skew. `AnchoredPriceProvider` is the designated standard provider for public pools. Any public pool on L2 using it will be broken under normal oracle update cadence.

---

### Recommendation

Add an `AnchoredPriceProviderL2` variant that accepts a `FUTURE_TOLERANCE` immutable (bounded to ≤ 1 hour, matching the existing L2 providers) and passes it to a four-argument `_isStale` identical to the one in `PriceProviderL2`. Alternatively, merge the L2-aware staleness logic into `AnchoredPriceProvider` directly with `FUTURE_TOLERANCE = 0` as the L1 default.

---

### Proof of Concept

1. Deploy `AnchoredPriceProvider` on Base with a Pyth Lazer or Chainlink Data Streams oracle.
2. Push an oracle update whose `refTime` is 2 seconds ahead of the current Base sequencer `block.timestamp` (normal for off-chain pushers).
3. Call `pool.swap(...)` on any pool using this provider.
4. Execution path: `swap` → `_getBidAndAskPriceX64` → `getBidAndAskPrice` → `_getBidAndAskPrice` → `_readLeg` → `_isStale(refTime=T+2, nowTs=T, maxDelta=...)` → returns `true` → `ok = false` → returns `(0, type(uint128).max)` → `getBidAndAskPrice` reverts `FeedStalled` → pool reverts `PriceProviderFailed`.
5. Every subsequent swap reverts identically until the sequencer timestamp catches up — which may never happen if oracle updates are continuous.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L222-230)
```text
    function _isStale(
        uint256 refTime,
        uint256 nowTs,
        uint256 maxDelta
    ) internal pure returns (bool) {
        if (refTime == 0) return true;
        if (refTime > nowTs) return true;
        return (nowTs - refTime) > maxDelta;
    }
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

**File:** metric-core/contracts/MetricOmmPool.sol (L804-813)
```text
  function _getBidAndAskPriceX64() internal returns (uint128 bidPriceX64, uint128 askPriceX64) {
    address activePriceProvider = _resolvedPriceProvider();
    try IPriceProvider(activePriceProvider).getBidAndAskPrice() returns (uint128 bid, uint128 ask) {
      if (bid >= ask) revert BidGreaterThanAsk();
      if (bid == 0) revert BidIsZero();
      return (bid, ask);
    } catch (bytes memory reason) {
      revert PriceProviderFailed(reason);
    }
  }
```
