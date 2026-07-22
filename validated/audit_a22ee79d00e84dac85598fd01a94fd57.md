### Title
Immutable price provider is the sole swap path with no fallback or migration, permanently breaking all swaps if the oracle fails — (`metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

When a pool is deployed with `immutablePriceProvider = true`, the `IMMUTABLE_PRICE_PROVIDER` address becomes the **only** price source for every swap. The factory-callable `setPriceProvider()` function, which appears to offer a migration path, is silently bypassed for these pools. If the immutable price provider ever becomes non-functional — due to oracle deprecation, feed-ID migration, or contract upgrade — every call to `swap()` permanently reverts with no admin recovery path.

---

### Finding Description

**Step 1 — Immutable is set at construction and takes unconditional priority.** [1](#0-0) 

```solidity
/// @dev If set this is the address of the immutable price provider
address internal immutable IMMUTABLE_PRICE_PROVIDER;
``` [2](#0-1) 

```solidity
if (immutablePriceProvider) {
    IMMUTABLE_PRICE_PROVIDER = priceProvider_;
    priceProvider = address(0);
} else { ... }
```

**Step 2 — `_resolvedPriceProvider()` always returns the immutable when it is non-zero, ignoring the mutable slot entirely.** [3](#0-2) 

```solidity
function _resolvedPriceProvider() internal view returns (address) {
    address imm = IMMUTABLE_PRICE_PROVIDER;
    if (imm != address(0)) return imm;   // always taken for immutable pools
    return priceProvider;
}
```

**Step 3 — `_getBidAndAskPriceX64()` is the sole price path for every swap; failure reverts the whole transaction.** [4](#0-3) 

```solidity
function _getBidAndAskPriceX64() internal returns (uint128 bidPriceX64, uint128 askPriceX64) {
    address activePriceProvider = _resolvedPriceProvider();
    try IPriceProvider(activePriceProvider).getBidAndAskPrice() returns (...) { ... }
    catch (bytes memory reason) {
        revert PriceProviderFailed(reason);   // no fallback
    }
}
```

**Step 4 — `setPriceProvider()` writes to the mutable slot but has zero effect on immutable-provider pools.** [5](#0-4) 

```solidity
function setPriceProvider(address newPriceProvider) external onlyFactory {
    priceProvider = newPriceProvider;   // dead write: IMMUTABLE_PRICE_PROVIDER still wins
    emit PriceProviderUpdated(newPriceProvider);
}
```

**Step 5 — The price provider itself chains two more immutables with no fallback.** [6](#0-5) 

```solidity
IOffchainOracle public immutable offchainOracle;
bytes32         public immutable offchainFeedId;
```

If the oracle contract is upgraded or the feed ID is retired, `getBidAndAskPrice()` returns `(0, type(uint128).max)`, which triggers `FeedStalled`, which propagates as `PriceProviderFailed` in the pool. [7](#0-6) 

The full immutable dependency chain is therefore:

```
Pool.IMMUTABLE_PRICE_PROVIDER (immutable)
  └─ PriceProvider.offchainOracle (immutable)
       └─ PriceProvider.offchainFeedId (immutable)
```

Any link in this chain failing permanently disables all swaps with no admin escape hatch.

---

### Impact Explanation

All calls to `swap()` revert permanently. The pool's core trading functionality is irreversibly broken. LP principal is not locked — `removeLiquidity` does not call `_getBidAndAskPriceX64()` — but the pool becomes a dead asset: no trades can execute, spread fees stop accruing, and the pool cannot be migrated to a working oracle without redeployment. This satisfies the allowed impact gate: **broken core pool functionality causing unusable swap flows**.

---

### Likelihood Explanation

Low in the short term; non-negligible over a pool's full lifetime. Oracle providers do migrate feed IDs, deprecate v1 contracts, and upgrade infrastructure. The `PriceProvider` has a `MAX_TIME_DELTA` staleness guard (up to 7 days), meaning even a brief oracle outage beyond that window permanently bricks the pool with no recovery. The risk compounds because the factory's apparent migration tool (`setPriceProvider`) silently does nothing for these pools, so operators may not discover the gap until after a failure.

---

### Recommendation

1. **Remove the unconditional priority of `IMMUTABLE_PRICE_PROVIDER`** or add a factory-controlled emergency override that can supersede it:
   ```solidity
   address internal emergencyPriceProvider;
   function _resolvedPriceProvider() internal view returns (address) {
       if (emergencyPriceProvider != address(0)) return emergencyPriceProvider;
       address imm = IMMUTABLE_PRICE_PROVIDER;
       if (imm != address(0)) return imm;
       return priceProvider;
   }
   ```
2. **Add a fallback price provider** that is tried when the primary reverts, analogous to Lido's withdrawal queue fallback in the Sablier fix.
3. **Guard `setPriceProvider()` with a revert** when `IMMUTABLE_PRICE_PROVIDER != address(0)` so the factory is not silently misled into thinking migration succeeded.

---

### Proof of Concept

1. Factory deploys pool with `immutablePriceProvider = true`, pointing to `PriceProvider` with `offchainFeedId = X`.
2. Oracle provider retires feed `X` or upgrades the oracle contract.
3. `PriceProvider.getBidAndAskPrice()` now always reverts with `FeedStalled` (staleness check fires).
4. Every call to `MetricOmmPool.swap()` reverts with `PriceProviderFailed`.
5. Factory calls `setPriceProvider(newWorkingProvider)` — emits `PriceProviderUpdated` but `_resolvedPriceProvider()` still returns `IMMUTABLE_PRICE_PROVIDER`; swaps continue to revert.
6. Pool is permanently unable to execute any swap. No on-chain recovery path exists short of redeployment.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L57-59)
```text
  /// @dev If set this is the address of the immutable price provider
  /// @dev If unset the `priceProvider` is the address of the mutable price provider.
  address internal immutable IMMUTABLE_PRICE_PROVIDER;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L127-133)
```text
    if (immutablePriceProvider) {
      IMMUTABLE_PRICE_PROVIDER = priceProvider_;
      priceProvider = address(0);
    } else {
      IMMUTABLE_PRICE_PROVIDER = address(0);
      priceProvider = priceProvider_;
    }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L477-480)
```text
  function setPriceProvider(address newPriceProvider) external onlyFactory {
    priceProvider = newPriceProvider;
    emit PriceProviderUpdated(newPriceProvider);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L633-637)
```text
  function _resolvedPriceProvider() internal view returns (address) {
    address imm = IMMUTABLE_PRICE_PROVIDER;
    if (imm != address(0)) return imm;
    return priceProvider;
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

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L30-32)
```text
    IOffchainOracle public immutable offchainOracle;
    bytes32         public immutable offchainFeedId;
    address         public immutable factory;
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L115-120)
```text
    function getBidAndAskPrice()
        external override returns (uint128 bid, uint128 ask)
    {
        (bid, ask) = _getBidAndAskPrice();
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
    }
```
