### Title
Mutable Price Provider Accepts Upgradeable Proxy Contracts Without Upgrade-Guard, Enabling Post-Validation Bid/Ask Manipulation — (File: `metric-core/contracts/MetricOmmPoolFactory.sol`)

---

### Summary

`_validatePriceProvider` only checks that the price provider returns the correct `token0()`/`token1()` addresses. It does not verify whether the price provider is an upgradeable proxy. A pool admin can legitimately set a proxy-based price provider (e.g., a Chainlink aggregator proxy) that passes validation at proposal and execution time. If that proxy is later upgraded by its owner to return manipulated `getBidAndAskPrice()` values, every subsequent swap in the pool executes at the corrupted price — with no on-chain mechanism to detect or block it.

---

### Finding Description

`MetricOmmPoolFactory._validatePriceProvider` is the sole gate for price-provider acceptance, called at pool creation, at `proposePoolPriceProvider`, and again at `executePoolPriceProviderUpdate`:

```solidity
// metric-core/contracts/MetricOmmPoolFactory.sol  lines 541-546
function _validatePriceProvider(address token0, address token1, address priceProvider) internal view {
    if (priceProvider == address(0)) revert InvalidPriceProvider();
    if (IPriceProvider(priceProvider).token0() != token0 || IPriceProvider(priceProvider).token1() != token1) {
      revert PriceProviderTokenMismatch();
    }
}
```

The check is purely a token-pair identity check. A proxy contract whose `token0()`/`token1()` return the correct addresses passes unconditionally, regardless of whether its implementation can be swapped out.

After the price provider is accepted and stored in the pool's mutable `priceProvider` slot, every swap calls:

```solidity
// metric-core/contracts/MetricOmmPool.sol  lines 804-813
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

The only runtime guards are `bid >= ask` and `bid == 0`. Any `(bid, ask)` pair satisfying `0 < bid < ask` is accepted without further bounds checking. A malicious upgrade of the proxy can return any such pair — e.g., a bid/ask spread that is 10× wider than the true market, or prices that are systematically biased in one direction — and the pool will execute all swaps at those prices.

The corrupted prices flow directly into `SwapMath.midAndSpreadFeeX64FromBidAsk`, which computes the mid-price and base fee used for every bin step:

```solidity
// metric-core/contracts/MetricOmmPool.sol  lines 242-245
(uint256 midPriceX64, uint256 baseFeeX64) =
  SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
SwapMath.InternalSwapParams memory params =
  SwapMath.InternalSwapParams({midPriceX64: midPriceX64, baseFeeX64: baseFeeX64, priceLimitX64: priceLimitX64});
```

All bin price bounds (`lowerPriceX64`, `upperPriceX64`) and the marginal execution price are derived from `midPriceX64`, so a corrupted oracle price propagates to every token amount computed in `_executeSwap`.

---

### Impact Explanation

A maliciously upgraded proxy price provider can:

1. **Drain traders**: Return a mid-price far below the true market for a `zeroForOne` swap, causing the pool to give the trader far fewer token1 than they are owed for their token0 input. The difference stays in the pool but is credited to no LP — it is effectively stolen from the trader.
2. **Drain the pool**: Return a mid-price far above the true market for a `!zeroForOne` swap, causing the pool to pay out more token0 than the trader's token1 input is worth, eventually exhausting `binTotals.scaledToken0` and making the pool insolvent for LP withdrawals.
3. **Corrupt LP claims**: Because `binTotals` and per-bin balances are updated based on the swap deltas computed from the corrupted price, LP share redemption values are permanently distorted.

All three outcomes satisfy the contest's "Critical/High direct loss of user principal, protocol fees, or owed LP assets" and "Pool insolvency" impact criteria.

---

### Likelihood Explanation

- Many production oracle deployments (Chainlink, Pyth wrappers, custom aggregators) use upgradeable proxy patterns. A pool admin acting in good faith can legitimately point a pool at such a proxy.
- The timelock on `proposePoolPriceProvider` / `executePoolPriceProviderUpdate` protects against the pool admin *changing* the price provider, but provides zero protection once a proxy price provider is already active and its implementation is upgraded by its own owner.
- The initial price provider set at pool creation is also subject to this: `_validatePriceProvider` is called once at `createPool`, and if that provider is a proxy, any subsequent upgrade is invisible to the pool.
- No on-chain mechanism detects or reverts on a proxy upgrade; the pool continues operating silently at corrupted prices.

---

### Recommendation

1. **Document the proxy restriction**: Add an explicit NatSpec warning to `_validatePriceProvider` and `proposePoolPriceProvider` stating that upgradeable proxy price providers must not be used, mirroring the recommendation in the external report.
2. **Optionally enforce non-upgradeability**: Consider calling a standard interface (e.g., ERC-1967 `implementation()` slot check or OpenZeppelin's `UUPSUpgradeable` detection) to revert if the price provider is a known proxy pattern.
3. **Prefer immutable price providers**: Encourage pool creators to set `priceProviderTimelock = type(uint256).max` (immutable mode) whenever the oracle is a proxy, so the pool cannot be silently re-pointed.

---

### Proof of Concept

```
1. Deploy a proxy price provider `ProxyPP` whose `token0()`/`token1()` return the pool's tokens
   and whose initial `getBidAndAskPrice()` returns a fair market bid/ask.

2. Pool admin calls `proposePoolPriceProvider(pool, ProxyPP)`.
   → `_validatePriceProvider` passes (token addresses match).

3. After the timelock elapses, pool admin calls `executePoolPriceProviderUpdate(pool)`.
   → `_validatePriceProvider` passes again. `ProxyPP` is now the active price provider.

4. The proxy owner upgrades `ProxyPP`'s implementation to `MaliciousPP`, which returns:
     bid  = 1          (near-zero)
     ask  = 2          (satisfies bid < ask, bid > 0)
   This makes `midPriceX64` ≈ sqrt(1*2) ≈ 1, far below the true market price.

5. Any trader calling `swap(recipient, false, amountSpecified, ...)` (token1 → token0)
   now receives token0 valued at ~1 unit of price instead of the true market price.
   The pool's `binTotals.scaledToken0` is drained at a fraction of fair value,
   leaving LPs unable to recover their principal.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L474-507)
```text
  function proposePoolPriceProvider(address pool, address newPriceProvider)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
    PoolImmutables memory p = IMetricOmmPool(pool).getImmutables();
    uint256 timelock = priceProviderTimelock[pool];
    if (p.immutablePriceProvider != address(0)) revert PriceProviderImmutable();
    _validatePriceProvider(p.token0, p.token1, newPriceProvider);

    address mutableProvider = PoolStateLibrary._slot3(pool);
    address current = mutableProvider != address(0) ? mutableProvider : p.immutablePriceProvider;
    uint256 executeAfter = block.timestamp + timelock;
    pendingPriceProvider[pool] = newPriceProvider;
    pendingPriceProviderExecuteAfter[pool] = executeAfter;
    emit PoolPriceProviderChangeProposed(pool, current, newPriceProvider, executeAfter);
  }

  /// @inheritdoc IMetricOmmPoolFactoryPoolAdmin
  function executePoolPriceProviderUpdate(address pool) external override nonReentrant onlyPoolAdmin(pool) {
    address pending = pendingPriceProvider[pool];
    if (pending == address(0)) revert NoPriceProviderChangeProposed();
    uint256 execAfter = pendingPriceProviderExecuteAfter[pool];
    // forge-lint: disable-next-line(block-timestamp) -- timelock enforcement legitimately relies on `block.timestamp`.
    if (block.timestamp < execAfter) revert PriceProviderTimelockNotElapsed(execAfter, block.timestamp);
    PoolImmutables memory p = IMetricOmmPool(pool).getImmutables();
    if (p.immutablePriceProvider != address(0)) revert PriceProviderImmutable();
    _validatePriceProvider(p.token0, p.token1, pending);
    IMetricOmmPoolFactoryActions(pool).setPriceProvider(pending);
    delete pendingPriceProvider[pool];
    delete pendingPriceProviderExecuteAfter[pool];
    emit PoolPriceProviderUpdated(pool, pending);
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L541-546)
```text
  function _validatePriceProvider(address token0, address token1, address priceProvider) internal view {
    if (priceProvider == address(0)) revert InvalidPriceProvider();
    if (IPriceProvider(priceProvider).token0() != token0 || IPriceProvider(priceProvider).token1() != token1) {
      revert PriceProviderTokenMismatch();
    }
  }
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

**File:** metric-core/contracts/MetricOmmPool.sol (L242-248)
```text
    (uint256 midPriceX64, uint256 baseFeeX64) =
      SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
    SwapMath.InternalSwapParams memory params =
      SwapMath.InternalSwapParams({midPriceX64: midPriceX64, baseFeeX64: baseFeeX64, priceLimitX64: priceLimitX64});

    (int256 amount0Delta, int256 amount1Delta, uint256 protocolFeeAmount) =
      _executeSwap(zeroForOne, amountSpecified, params);
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
