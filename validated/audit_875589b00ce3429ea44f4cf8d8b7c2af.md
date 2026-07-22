### Title
Broken `IMMUTABLE_PRICE_PROVIDER` permanently locks all pool swaps with no admin recovery path — (`metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

When a pool is deployed with `immutablePriceProvider = true`, the price provider address is stored in the `IMMUTABLE_PRICE_PROVIDER` immutable. If that external contract begins reverting (oracle deprecation, contract upgrade, feed deregistration), every call to `swap()` and `getSellAndBuyPrices()` is permanently bricked. The factory's `setPriceProvider()` path exists but is silently bypassed by `_resolvedPriceProvider()`, leaving no on-chain recovery mechanism.

---

### Finding Description

`_getBidAndAskPriceX64()` is called unconditionally at the top of every `swap()` execution: [1](#0-0) 

```solidity
function _getBidAndAskPriceX64() internal returns (uint128 bidPriceX64, uint128 askPriceX64) {
    address activePriceProvider = _resolvedPriceProvider();
    try IPriceProvider(activePriceProvider).getBidAndAskPrice() returns (uint128 bid, uint128 ask) {
      ...
    } catch (bytes memory reason) {
      revert PriceProviderFailed(reason);   // ← re-reverts; no fallback
    }
}
```

`_resolvedPriceProvider()` always returns the immutable when it is non-zero: [2](#0-1) 

```solidity
function _resolvedPriceProvider() internal view returns (address) {
    address imm = IMMUTABLE_PRICE_PROVIDER;
    if (imm != address(0)) return imm;
    return priceProvider;
}
```

The factory-callable `setPriceProvider()` writes to the `priceProvider` storage slot: [3](#0-2) 

```solidity
function setPriceProvider(address newPriceProvider) external onlyFactory {
    priceProvider = newPriceProvider;
    emit PriceProviderUpdated(newPriceProvider);
}
```

Because `_resolvedPriceProvider()` short-circuits on `IMMUTABLE_PRICE_PROVIDER != address(0)`, the storage write from `setPriceProvider()` is **never read** for immutable-provider pools. There is no code path that allows the factory or pool admin to redirect the price query away from the broken immutable address.

---

### Impact Explanation

Every call to `swap()` passes through `_getBidAndAskPriceX64()`: [4](#0-3) 

If `IMMUTABLE_PRICE_PROVIDER` reverts, **all swaps revert permanently**. The pool's core trading functionality is completely unusable. `removeLiquidity` does not call the price provider, so LPs can still withdraw, but the pool is effectively dead as a trading venue and accrues no further fees. This matches the allowed impact gate: *"Broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows."*

---

### Likelihood Explanation

Price provider contracts are external dependencies subject to:
- Oracle feed deprecation or deregistration (e.g., `FeedNotFound` revert in the abuse-protection layer)
- Contract upgrades that change the interface
- Blacklisting of the pool address by the oracle's abuse-protection layer (the `OracleBase` in `smart-contracts-poc` can blacklist a pool, causing `price()` to revert with `Blacklisted`) [5](#0-4) 

Any of these events permanently bricks swaps on all pools that used `immutablePriceProvider = true` at deployment, with no admin remedy.

---

### Recommendation

1. **Remove the hard immutability or add an emergency override.** Even if the price provider is intended to be immutable under normal operation, the factory should be able to set an emergency override address that `_resolvedPriceProvider()` checks first.

2. **Alternatively**, make `_resolvedPriceProvider()` fall back to `priceProvider` storage when the immutable call fails, so `setPriceProvider()` already provides a recovery path.

3. **At minimum**, document that pools deployed with `immutablePriceProvider = true` have no recovery path if the price provider breaks, and ensure the oracle's abuse-protection layer cannot blacklist a registered pool without an on-chain remedy.

---

### Proof of Concept

1. Deploy a pool with `immutablePriceProvider = true`, pointing to a `ProtectedPriceProvider`.
2. Oracle admin calls `oracle.setBlacklist(pool, true)` — the price provider's `getBidAndAskPrice()` now reverts with `Blacklisted`.
3. Any call to `pool.swap(...)` reverts with `PriceProviderFailed`.
4. Factory calls `pool.setPriceProvider(newProvider)` — transaction succeeds and emits `PriceProviderUpdated`, but `_resolvedPriceProvider()` still returns `IMMUTABLE_PRICE_PROVIDER`.
5. `pool.swap(...)` continues to revert. The pool is permanently bricked for trading with no on-chain recovery.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L227-229)
```text
    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L29-39)
```text
    mapping(bytes32 => OracleData) internal oracleData;
    mapping(bytes32 => PriceGuard) public priceGuard;
    mapping(bytes32 => address) public pendingStateGuard;
    mapping(bytes32 => address) public stateGuard;

    // ── Read-access / abuse protection ──
    uint256 public registrationFee;
    EnumerableSet.AddressSet internal approvedFactories;
    EnumerableSet.AddressSet internal integrators;
    mapping(address => bool) public blacklisted;
    mapping(bytes32 => mapping(address => bool)) public registeredPool;
```
