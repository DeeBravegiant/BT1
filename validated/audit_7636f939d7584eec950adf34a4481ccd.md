Looking at the Metric OMM codebase for an analog to the Teller M-15 front-running fee vulnerability, I need to trace fee-change paths that can be executed without timelock and that directly affect swap output.

**Key observations:**

1. `setPoolBinAdditionalFees` in the factory has no cap check and no timelock on `addFeeBuyE6`/`addFeeSellE6` (both `uint16`, max ≈ 6.5% E6).
2. `setPoolAdminFees` has cap checks but no timelock on `adminNotionalFeeE8` (max 1%).
3. The `swap` function has `priceLimitX64` for price slippage but **no `maxFee` parameter**.
4. Per-bin fees are read from storage at swap execution time and directly increase the LP fee charged to the trader.

The price provider change was given a timelock (M01 fix), but fee changes have no equivalent protection.

---

### Title
Pool admin can front-run swaps by raising per-bin additional fees without timelock — (`metric-core/contracts/MetricOmmPoolFactory.sol`)

### Summary
A malicious or compromised pool admin can front-run any pending `swap` transaction by calling `setPoolBinAdditionalFees` to raise per-bin buy/sell fees to `type(uint16).max` (65 535 E6 ≈ 6.5%) with no timelock and no cap check. Because the `swap` function exposes only `priceLimitX64` for slippage protection and has no `maxFee` guard, the trader cannot consent to or reject the elevated fee, and receives materially less output (exact-input) or pays materially more input (exact-output) than quoted.

### Finding Description

`setPoolBinAdditionalFees` in `MetricOmmPoolFactory` forwards directly to the pool with no cap validation and no timelock: [1](#0-0) 

The pool's `setBinAdditionalFees` also applies no cap: [2](#0-1) 

At swap time, the per-bin fee is added directly to the base oracle-spread fee before computing the LP fee charged to the trader: [3](#0-2) 

In `SwapMath`, the gross input the trader must pay is `ceil(netIn × (ONE_X64 + currBinBuyFeeX64) / ONE_X64)`: [4](#0-3) 

Setting `addFeeBuyE6 = 65535` adds `≈ 6.55 %` on top of the oracle spread fee. The `swap` interface provides only `priceLimitX64`; there is no `maxFee` or `maxAdditionalFee` parameter: [5](#0-4) 

By contrast, the price-provider change — a less immediately harmful admin action — was given a mandatory timelock (M01 fix): [6](#0-5) 

Fee changes have no equivalent protection.

**Attack path:**
1. Pool admin observes a large pending `swap` (e.g., exact-input token1 → token0) in the mempool.
2. Pool admin front-runs with `setPoolBinAdditionalFees(pool, curBinIdx, 65535, 0)`.
3. Victim's swap executes; `currBinBuyFeeX64` is now `baseFeeX64 + 0.065535·ONE_X64`.
4. Trader receives up to ≈ 6.5 % less token0 than the pre-trade quote indicated, with no on-chain revert.

A secondary vector exists via `setPoolAdminFees` raising `adminNotionalFeeE8` to `maxAdminNotionalFeeE8` (up to 1 %), which is deducted directly from the output token: [7](#0-6) [8](#0-7) 

### Impact Explanation
A trader executing an exact-input swap can receive up to ≈ 6.5 % less output than the pre-trade quote. For exact-output swaps the trader pays up to ≈ 6.5 % more input. The corrupted value flows directly through `binState.token0BalanceScaled` / `token1BalanceScaled` updates and the `IncorrectDelta` callback check, which only verifies the pool received its owed input — it does not bound the fee charged. Real token balances are moved; LP claims are unaffected but the trader's principal is reduced.

### Likelihood Explanation
Medium. The pool admin is a semi-trusted role (analogous to a Teller market owner). The admin key can be an EOA, a compromised multisig, or a rogue operator. The attack requires only a single transaction with no special knowledge beyond mempool observation, and it is repeatable on every swap.

### Recommendation
1. **Add a timelock on per-bin fee changes** — mirror the `proposePoolPriceProvider` / `executePoolPriceProviderUpdate` pattern already used for oracle rotation.
2. **Add a `maxAdditionalFeeE6` parameter to `swap`** — revert if the current bin's `addFeeBuyE6` / `addFeeSellE6` exceeds the caller-supplied bound, giving traders explicit fee consent (analogous to the Teller fix of adding a max-fee parameter to `lenderAcceptBid`).
3. **Add a hard cap on `addFeeBuyE6` / `addFeeSellE6`** in `setPoolBinAdditionalFees` — e.g., enforce `≤ maxAdminSpreadFeeE6` or a dedicated constant.

### Proof of Concept
```
// 1. Pool admin front-runs a pending swap

### Citations

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

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L494-507)
```text
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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-224)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
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

**File:** metric-core/contracts/MetricOmmPool.sol (L750-762)
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
```

**File:** metric-core/contracts/MetricOmmPool.sol (L906-915)
```text
          (curPosInBinCache, delta0Scaled, delta1Scaled, binLpFeeAmountScaled) = SwapMath.buyToken0InBinSpecifiedOut(
            binState,
            curPosInBinCache,
            state,
            params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6),
            lowerPriceX64,
            upperPriceX64,
            params.priceLimitX64,
            spreadFeeE6
          );
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L326-330)
```text
  function grossInputWithBinFeeCeil(uint256 netInScaled, uint256 onePlusBinFeeX64) internal pure returns (uint256) {
    unchecked {
      return Math.ceilDiv(netInScaled * onePlusBinFeeX64, ONE_X64);
    }
  }
```
