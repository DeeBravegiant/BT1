### Title
`AnchoredPriceProvider` Uses L1-Only Staleness Check, Breaking All Swaps on L2 Deployments — (`smart-contracts-poc/contracts/AnchoredPriceProvider.sol`)

### Summary

`AnchoredPriceProvider` is described as "the one standard provider for public pools." It contains an L1-only `_isStale` implementation that unconditionally treats any oracle `refTime` ahead of `block.timestamp` as stale. The protocol explicitly deploys on L2 chains (Base, HyperEVM) and created L2-aware variants (`PriceProviderL2`, `ProtectedPriceProviderL2`) with a `FUTURE_TOLERANCE` parameter to handle sequencer clock skew — but no such variant exists for `AnchoredPriceProvider`. When an oracle's `refTime` is even one second ahead of `block.timestamp` on L2, every swap through any pool using `AnchoredPriceProvider` reverts.

### Finding Description

`AnchoredPriceProvider._isStale` (the L1 variant):

```solidity
// AnchoredPriceProvider.sol lines 222-230
function _isStale(
    uint256 refTime,
    uint256 nowTs,
    uint256 maxDelta
) internal pure returns (bool) {
    if (refTime == 0) return true;
    if (refTime > nowTs) return true;   // ← any future refTime is stale
    return (nowTs - refTime) > maxDelta;
}
``` [1](#0-0) 

The L2-aware variant in `PriceProviderL2` tolerates clock skew via `FUTURE_TOLERANCE`:

```solidity
// PriceProviderL2.sol lines 135-150
function _isStale(uint256 refTime, uint256 nowTs, uint256 maxDelta, uint256 futureTol)
    internal pure returns (bool) {
    if (refTime == 0) return true;
    if (refTime > nowTs) {
        return (refTime - nowTs) > futureTol;   // ← tolerate within futureTol
    }
    return (nowTs - refTime) > maxDelta;
}
``` [2](#0-1) 

`AnchoredPriceProvider` has no L2 variant. The `smart-contracts-poc/contracts` directory contains `PriceProviderL2.sol`, `ProtectedPriceProviderL2.sol`, and `PriceProviderFactoryL2.sol`, but no `AnchoredPriceProviderL2.sol`.



The failure chain when `refTime > block.timestamp` on L2:

1. `_readLeg` calls `_isStale(refTime, block.timestamp, MAX_REF_STALENESS)` → returns `true`
2. `_readLeg` returns `ok = false`
3. `_getBidAndAskPrice` returns `(0, type(uint128).max)`
4. `getBidAndAskPrice` reverts with `FeedStalled()`
5. `MetricOmmPool._getBidAndAskPriceX64` catches and reverts with `PriceProviderFailed`
6. Every call to `swap` reverts [3](#0-2) [4](#0-3) 

### Impact Explanation

All swaps through any pool whose price provider is an `AnchoredPriceProvider` instance become non-functional on L2 whenever the oracle's `refTime` exceeds `block.timestamp`. Since `AnchoredPriceProvider` is the designated standard provider for public pools, this breaks the primary trading path for the entire protocol on L2. No funds are directly stolen, but the swap flow is completely unusable during affected windows, matching the "broken core pool functionality causing unusable swap flows" impact gate.

### Likelihood Explanation

On L2 chains (Base, HyperEVM), sequencer block timestamps and oracle publication timestamps are not perfectly synchronized. Pyth and Chainlink publish prices with timestamps that can be slightly ahead of the sequencer's `block.timestamp`. The protocol already acknowledged this by building `FUTURE_TOLERANCE` into `PriceProviderL2` and `ProtectedPriceProviderL2`. The omission of the same treatment in `AnchoredPriceProvider` is an oversight that will trigger on any L2 deployment whenever the oracle timestamp leads the block timestamp, which is a routine occurrence.

### Recommendation

Add an `AnchoredPriceProviderL2` variant (or add an optional `FUTURE_TOLERANCE` immutable to `AnchoredPriceProvider`) that mirrors the L2-aware `_isStale` logic already present in `PriceProviderL2`:

```solidity
function _isStale(uint256 refTime, uint256 nowTs, uint256 maxDelta, uint256 futureTol)
    internal pure returns (bool) {
    if (refTime == 0) return true;
    if (refTime > nowTs) return (refTime - nowTs) > futureTol;
    return (nowTs - refTime) > maxDelta;
}
```

Pass `FUTURE_TOLERANCE` (set at construction, bounded to `<= 1 hours` as in `PriceProviderL2`) into `_readLeg` and `_isStale` calls.

### Proof of Concept

1. Deploy `AnchoredPriceProvider` on Base pointing to a Pyth feed.
2. Pyth publishes a price update with `publishTime = block.timestamp + 2` (sequencer clock is 2 s behind oracle).
3. `_readLeg` calls `_isStale(block.timestamp + 2, block.timestamp, MAX_REF_STALENESS)`.
4. `refTime > nowTs` → returns `true` → `ok = false`.
5. `_getBidAndAskPrice` returns `(0, type(uint128).max)`.
6. `getBidAndAskPrice` reverts `FeedStalled()`.
7. Pool `swap` reverts `PriceProviderFailed(...)`.
8. All swaps are blocked until the sequencer's clock catches up. [5](#0-4)

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
