### Title
Pool Admin Can Frontrun Swappers by Instantly Raising `notionalFeeE8` With No Timelock, Causing Swappers to Pay More Than Expected - (`metric-core/contracts/MetricOmmPoolFactory.sol`)

### Summary

`setPoolAdminFees` allows the pool admin to raise `notionalFeeE8` to the cap (1%) in a single transaction with no timelock. Because `swap` reads `notionalFeeE8` from storage at execution time and the swap interface exposes no `maxFee` guard, a malicious pool admin can frontrun any pending swap to extract up to 1% of the swap notional from the swapper.

### Finding Description

`setPoolAdminFees` in `MetricOmmPoolFactory` updates the pool's `notionalFeeE8` immediately: [1](#0-0) 

The call chain is: `setPoolAdminFees` → `collectFees` (at old rates) → `poolFeeConfig` update → `setPoolFees` on the pool, which writes the new `notionalFeeE8` to pool storage in the same block with no delay. [2](#0-1) 

During `swap`, `_executeSwap` reads `notionalFeeE8` from storage and applies it directly to the swapper's output (exact-in) or input (exact-out): [3](#0-2) 

For exact-in `zeroForOne`, the notional fee reduces the token1 output the swapper receives:
```
notionalFeeScaled = uint256(-amount1DeltaScaled) * notionalFeeE8 / 1e8
amount1DeltaScaled += int256(notionalFeeScaled)   // swapper gets less
```

For exact-out, it increases the token0 input the swapper must pay:
```
notionalFeeScaled = feeExclusiveInputScaled * notionalFeeE8 / 1e8
amount0DeltaScaled += int256(notionalFeeScaled)   // swapper pays more
```

The `swap` function signature accepts no `maxFee` or `maxNotionalFee` parameter: [4](#0-3) 

By contrast, oracle rotation — a less financially sensitive operation — is protected by a mandatory timelock: [5](#0-4) 

Fee changes have no equivalent protection.

The hard cap for admin notional fee is 1% (`maxAdminNotionalFeeE8 = 1_000_000`): [6](#0-5) 

A pool admin starting at `notionalFeeE8 = 0` can raise it to 1% in one transaction, then frontrun a pending swap.

Additionally, `setPoolBinAdditionalFees` — also without a timelock or cap check — lets the pool admin raise per-bin `addFeeBuyE6`/`addFeeSellE6` up to `uint16.max` (6.5535%) immediately, compounding the attack surface: [7](#0-6) [8](#0-7) 

### Impact Explanation

A malicious pool admin observes a pending `swap` in the mempool, frontruns it with `setPoolAdminFees(pool, currentAdminSpread, maxAdminNotionalFeeE8)`, and the swap executes at the elevated fee. The swapper receives fewer output tokens (exact-in) or must pay more input tokens (exact-out) than they expected — a direct, real token loss of up to 1% of the swap notional per transaction. Swappers who interact directly with the pool (not through the router's `amountOutMinimum` guard) have no on-chain protection.

### Likelihood Explanation

The pool admin is a semi-trusted role distinct from the factory owner. It can be any address, including a malicious EOA or contract. The attack requires no special setup beyond being the pool admin, is executable in a single transaction, and is invisible to the swapper until the swap settles. On chains with a public mempool (Ethereum mainnet, most L2s), frontrunning is straightforward.

### Recommendation

1. **Add a timelock for fee changes** analogous to the oracle rotation timelock: require `proposePoolAdminFees` + `executePoolAdminFees` with a mandatory delay before the new `notionalFeeE8` takes effect.
2. **Add a `maxNotionalFeeE8` parameter to `swap`** so callers can specify the maximum fee they accept; revert if `notionalFeeE8 > maxNotionalFeeE8` at execution time.
3. **Cap `addFeeBuyE6`/`addFeeSellE6`** in `setPoolBinAdditionalFees` and apply the same timelock.

### Proof of Concept

```solidity
// 1. Pool admin observes Alice's pending swap in the mempool:
//    pool.swap(alice, true, 1_000_000e18, 0, "", "")
//    Pool currently has notionalFeeE8 = 0.

// 2. Pool admin frontruns with higher gas:
factory.setPoolAdminFees(pool, currentAdminSpread, 1_000_000); // raise notional to 1%

// 3. Alice's swap executes at the elevated fee:
//    notionalFeeScaled = output * 1_000_000 / 1e8 = output * 1%
//    Alice receives 1% fewer tokens than she expected.

// 4. Pool admin collects the extra 1% via collectPoolFees.
factory.collectPoolFees(pool);
```

The `notionalFeeE8` is read from storage inside `_executeSwap` at lines 756/766/777/785 of `MetricOmmPool.sol`, after the fee was already raised in step 2, so Alice has no recourse. [9](#0-8)

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L44-45)
```text
  uint24 internal constant HARD_MAX_SPREAD_FEE_E6 = 200_000;
  uint24 internal constant HARD_MAX_NOTIONAL_FEE_E8 = 1_000_000;
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L408-435)
```text
  function setPoolAdminFees(address pool, uint24 newAdminSpreadFeeE6, uint24 newAdminNotionalFeeE8)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
    if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();

    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool)
      .collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]
      );

    c.adminSpreadFeeE6 = newAdminSpreadFeeE6;
    c.adminNotionalFeeE8 = newAdminNotionalFeeE8;
    poolFeeConfig[pool] = c;

    IMetricOmmPoolFactoryActions(pool)
      .setPoolFees(c.protocolSpreadFeeE6 + c.adminSpreadFeeE6, c.protocolNotionalFeeE8 + c.adminNotionalFeeE8);
    emit PoolAdminSpreadFeeUpdated(pool, newAdminSpreadFeeE6);
    emit PoolAdminNotionalFeeUpdated(pool, newAdminNotionalFeeE8);
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L450-457)
```text
  function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L487-490)
```text
    uint256 executeAfter = block.timestamp + timelock;
    pendingPriceProvider[pool] = newPriceProvider;
    pendingPriceProviderExecuteAfter[pool] = executeAfter;
    emit PoolPriceProviderChangeProposed(pool, current, newPriceProvider, executeAfter);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L437-451)
```text
  function setPoolFees(uint24 newSpreadFeeE6, uint24 newNotionalFeeE8)
    external
    onlyFactory
    nonReentrant(PoolActions.SET_POOL_FEES)
  {
    unchecked {
      if (newSpreadFeeE6 != spreadFeeE6) {
        spreadFeeE6 = newSpreadFeeE6;
        emit SpreadFeeUpdated(newSpreadFeeE6);
      }
      if (newNotionalFeeE8 != notionalFeeE8) {
        notionalFeeE8 = newNotionalFeeE8;
        emit NotionalFeeUpdated(newNotionalFeeE8);
      }
    }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L464-474)
```text
  function setBinAdditionalFees(int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external
    onlyFactory
    nonReentrant(PoolActions.SET_BIN_ADDITIONAL_FEES)
  {
    if (bin < LOWEST_BIN || bin > HIGHEST_BIN) revert InvalidBinIndex(bin);
    BinState storage s = _binStates[bin];
    s.addFeeBuyE6 = addFeeBuyE6;
    s.addFeeSellE6 = addFeeSellE6;
    emit BinAdditionalFeesUpdated(bin, addFeeBuyE6, addFeeSellE6);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L750-793)
```text
      if (notionalFeeE8 > 0) {
        if (amountSpecified > 0) {
          // exact in: notional fee on output token
          if (zeroForOne) {
            // safe because amount1DeltaScaled is bounded by uint128 total scaled token1 in bins.
            // forge-lint: disable-next-line(unsafe-typecast)
            uint256 notionalFeeScaled = uint256(-amount1DeltaScaled) * notionalFeeE8 / 1e8;
            if (notionalFeeScaled > 0) {
              // safe because notionalFeeScaled is bounded by uint128
              // forge-lint: disable-next-line(unsafe-typecast)
              amount1DeltaScaled = amount1DeltaScaled + int256(notionalFeeScaled);
              notionalFeeToken1Scaled = (uint256(notionalFeeToken1Scaled) + notionalFeeScaled).toUint128();
            }
          } else {
            // safe because amount0DeltaScaled is bounded by uint128 total scaled token0 in bins.
            // forge-lint: disable-next-line(unsafe-typecast)
            uint256 notionalFeeScaled = uint256(-amount0DeltaScaled) * notionalFeeE8 / 1e8;
            if (notionalFeeScaled > 0) {
              // safe because notionalFeeScaled is bounded by uint128
              // forge-lint: disable-next-line(unsafe-typecast)
              amount0DeltaScaled = amount0DeltaScaled + int256(notionalFeeScaled);
              notionalFeeToken0Scaled = (uint256(notionalFeeToken0Scaled) + notionalFeeScaled).toUint128();
            }
          }
        } else {
          // Exact-out: notional fee on pre-bin-fee input notional, added to input token.
          if (zeroForOne) {
            uint256 notionalFeeScaled = feeExclusiveInputScaled * notionalFeeE8 / 1e8;
            if (notionalFeeScaled > 0) {
              // safe because notionalFeeScaled is bounded by uint128
              // forge-lint: disable-next-line(unsafe-typecast)
              amount0DeltaScaled = amount0DeltaScaled + int256(notionalFeeScaled);
              notionalFeeToken0Scaled = (uint256(notionalFeeToken0Scaled) + notionalFeeScaled).toUint128();
            }
          } else {
            uint256 notionalFeeScaled = feeExclusiveInputScaled * notionalFeeE8 / 1e8;
            if (notionalFeeScaled > 0) {
              // safe because notionalFeeScaled is bounded by uint128
              // forge-lint: disable-next-line(unsafe-typecast)
              amount1DeltaScaled = amount1DeltaScaled + int256(notionalFeeScaled);
              notionalFeeToken1Scaled = (uint256(notionalFeeToken1Scaled) + notionalFeeScaled).toUint128();
            }
          }
        }
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L188-195)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external returns (int128 amount0Delta, int128 amount1Delta);
```
