### Title
Notional Fee Double-Counted in `collectFees`: Spread Fee Computed on Surplus That Already Includes Notional Accumulator, Draining LP Funds — (`metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`MetricOmmPool.collectFees()` computes the spread-fee payout as 100% of the pool's token surplus (`balance − binTotals`), but that surplus already contains the accumulated notional fees. The notional accumulator is then paid out a second time on top. When both fee types are active, the total transfer exceeds the true fee surplus by exactly the notional accumulator amount, and the shortfall is silently drawn from LP principal.

---

### Finding Description

**How notional fees enter the surplus**

During a swap, the notional fee is charged as extra input from the trader and added to `amount0DeltaScaled` / `amount1DeltaScaled`, but it is **not** credited to `binTotals`: [1](#0-0) 

The notional amount therefore sits in the pool's ERC-20 balance but is absent from `binTotals.scaledToken0/1`. It is tracked separately in `notionalFeeToken0Scaled` / `notionalFeeToken1Scaled`.

**How `collectFees` computes the spread-fee payout** [2](#0-1) 

`surplus0Scaled = balance0 × scale − binTotals.scaledToken0`

Because the notional accumulator is never subtracted from `binTotals`, the surplus equals **spread-fee residual + notional-fee residual**. The spread-fee split then pays out:

```
spreadFee0ToAdmin   = surplus × adminSpreadFeeE6   / spreadSumE6
spreadFee0ToProtocol= surplus × protocolSpreadFeeE6 / spreadSumE6
```

Since `adminSpreadFeeE6 + protocolSpreadFeeE6 = spreadSumE6`, the two terms sum to **100% of surplus** — i.e., the entire notional residual is consumed here as if it were spread-fee revenue.

**The notional accumulator is then paid out again** [3](#0-2) 

```
notionalFee0ToProtocol = notionalFeeToken0Scaled − notionalFee0ToAdmin
```

Total token0 transferred out:

```
= surplus0Scaled + notionalFeeToken0Scaled
= (spreadFeeResidual + notionalResidual) + notionalResidual
= spreadFeeResidual + 2 × notionalResidual
```

The excess `notionalResidual` is drawn from LP principal (`binTotals`), making the pool insolvent for LP withdrawals.

**The test suite explicitly documents this over-allocation** [4](#0-3) 

```solidity
assertGt(total0Attempted, surplus0Scaled, "token0 attempted payout exceeds computed surplus");
assertGt(total1Attempted, surplus1Scaled, "token1 attempted payout exceeds computed surplus");
```

The test is named `test_collectProtocolFees_math_overallocates_whenSpreadAndNotionalBothActive` and asserts the over-allocation as a fact, confirming the bug is present in production code.

---

### Impact Explanation

Every call to `collectFees` when both `spreadFeeE6 > 0` and `notionalFeeE8 > 0` transfers `notionalFeeToken0Scaled` (and/or `notionalFeeToken1Scaled`) extra tokens beyond the true fee surplus. These tokens come from LP principal tracked in `binTotals`. After collection, `binTotals` overstates the pool's actual token balance, so LP `removeLiquidity` calls will revert or receive less than their entitled share — direct loss of LP principal.

---

### Likelihood Explanation

Both fee types are independently configurable by the factory owner via `setPoolProtocolFee` and `setPoolAdminFee`. Any pool where an admin enables a non-zero notional fee alongside a non-zero spread fee is immediately vulnerable. The factory owner is semi-trusted (not fully trusted per contest scope), and pool admins are explicitly in scope. The trigger is a routine `collectFees` call, which any authorized caller can invoke.

---

### Recommendation

Before computing the spread-fee split, subtract the notional accumulator from the surplus so that each fee type is paid from its own residual:

```solidity
// In collectFees, before spread-fee computation:
uint256 spreadSurplus0Scaled = surplus0Scaled > notionalFeeToken0Scaled
    ? surplus0Scaled - notionalFeeToken0Scaled : 0;
uint256 spreadSurplus1Scaled = surplus1Scaled > notionalFeeToken1Scaled
    ? surplus1Scaled - notionalFeeToken1Scaled : 0;

// Use spreadSurplus*Scaled instead of surplus*Scaled for spread-fee splits.
```

This ensures `spreadFee + notionalFee ≤ actual surplus` and LP principal is never touched.

---

### Proof of Concept

1. Deploy a pool with `adminSpreadFeeE6 = 500_000` (50%), `protocolSpreadFeeE6 = 500_000` (50%), and `notionalFeeE8 = 1_000_000` (1%).
2. Add liquidity and execute several swaps so that both `notionalFeeToken0Scaled > 0` and a spread-fee surplus accumulates.
3. Record `binTotals.scaledToken0` (= LP principal) and `balance0` before `collectFees`.
4. Call `collectFees`.
5. Observe that `balance0_after − binTotals.scaledToken0_after < 0`, i.e., the pool's ERC-20 balance is now less than what `binTotals` claims LPs are owed.
6. Attempt LP `removeLiquidity` — the callback payment check (`InsufficientTokenBalance`) fires, or LPs receive fewer tokens than their share entitles them to.

The test `test_collectProtocolFees_math_overallocates_whenSpreadAndNotionalBothActive` already demonstrates step 4–5 arithmetically without requiring a custom harness. [5](#0-4) [6](#0-5) [4](#0-3)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L391-433)
```text
      uint256 spreadFee0ToAdminScaled = spreadSumE6 == 0 ? 0 : (surplus0Scaled * adminSpreadFeeE6_) / spreadSumE6;
      uint256 spreadFee1ToAdminScaled = spreadSumE6 == 0 ? 0 : (surplus1Scaled * adminSpreadFeeE6_) / spreadSumE6;

      uint256 spreadFee0ToProtocolScaled = spreadSumE6 == 0 ? 0 : (surplus0Scaled * protocolSpreadFeeE6_) / spreadSumE6;
      uint256 spreadFee1ToProtocolScaled = spreadSumE6 == 0 ? 0 : (surplus1Scaled * protocolSpreadFeeE6_) / spreadSumE6;

      uint256 notionalFee0ToAdminScaled =
        notionalSumE8 == 0 ? 0 : (notionalFee0AmountScaled * adminNotionalFeeE8_) / notionalSumE8;
      uint256 notionalFee1ToAdminScaled =
        notionalSumE8 == 0 ? 0 : (notionalFee1AmountScaled * adminNotionalFeeE8_) / notionalSumE8;

      uint256 notionalFee0ToProtocolScaled = notionalFee0AmountScaled - notionalFee0ToAdminScaled;
      uint256 notionalFee1ToProtocolScaled = notionalFee1AmountScaled - notionalFee1ToAdminScaled;

      uint256 totalFee0ToAdminScaled = spreadFee0ToAdminScaled + notionalFee0ToAdminScaled;
      uint256 totalFee1ToAdminScaled = spreadFee1ToAdminScaled + notionalFee1ToAdminScaled;

      uint256 totalFee0ToProtocolScaled = spreadFee0ToProtocolScaled + notionalFee0ToProtocolScaled;
      uint256 totalFee1ToProtocolScaled = spreadFee1ToProtocolScaled + notionalFee1ToProtocolScaled;

      (uint256 totalFee0ToAdmin, uint256 totalFee1ToAdmin) =
        deltasScaledToExternal(totalFee0ToAdminScaled, totalFee1ToAdminScaled, Math.Rounding.Floor);
      (uint256 totalFee0ToProtocol, uint256 totalFee1ToProtocol) =
        deltasScaledToExternal(totalFee0ToProtocolScaled, totalFee1ToProtocolScaled, Math.Rounding.Floor);

      if (totalFee0ToAdmin > 0) {
        transferToken0(adminFeeDestination_, totalFee0ToAdmin);
      }
      if (totalFee1ToAdmin > 0) {
        transferToken1(adminFeeDestination_, totalFee1ToAdmin);
      }
      if (totalFee0ToProtocol > 0) {
        transferToken0(FACTORY, totalFee0ToProtocol);
      }
      if (totalFee1ToProtocol > 0) {
        transferToken1(FACTORY, totalFee1ToProtocol);
      }

      notionalFeeToken0Scaled = 0;
      notionalFeeToken1Scaled = 0;

      emit ProtocolFeesCollected(totalFee0ToProtocol, totalFee1ToProtocol, totalFee0ToAdmin, totalFee1ToAdmin);
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

**File:** metric-core/test/MetricOmmPool.notionalFee.t.sol (L211-266)
```text
  function test_collectProtocolFees_math_overallocates_whenSpreadAndNotionalBothActive() public {
    pool.collectFees(PROTOCOL_FEE, ADMIN_FEE, 0, 0, adminFeeDestination);
    poolFeeConfig[address(pool)] = PoolFeeConfig({
      protocolSpreadFeeE6: PROTOCOL_FEE,
      adminSpreadFeeE6: ADMIN_FEE,
      protocolNotionalFeeE8: FEE_1_PCT_E8,
      adminNotionalFeeE8: 0
    });
    pool.setPoolFees(PROTOCOL_FEE + ADMIN_FEE, FEE_1_PCT_E8);

    _addLiquidity(1, -5, 4, 100_000, 0);
    for (uint256 i = 0; i < 8; i++) {
      _swap(0, users[0], false, int128(50_000), type(uint128).max);
      _swap(0, users[0], true, int128(10_000), 0);
    }

    (uint128 totalScaledToken0InBins, uint128 totalScaledToken1InBins) = PoolStateLibrary._slot1(_poolAddr());
    (uint128 notional0, uint128 notional1) = PoolStateLibrary._slot2(_poolAddr());
    assertGt(uint256(notional0) + uint256(notional1), 10, "notional accumulators should be non-zero");

    address adminAddr = IMetricOmmPoolFactory(factory).poolAdmin(_poolAddr());
    (uint24 protocolSpreadFeeE6, uint24 adminSpreadFeeE6,,) = IMetricOmmPoolFactory(factory).poolFeeConfig(_poolAddr());
    assertEq(adminAddr, admin);
    PoolFeeConfig memory feeConfig = poolFeeConfig[address(pool)];
    uint24 protocolNotionalFeeE8 = feeConfig.protocolNotionalFeeE8;
    uint24 adminNotionalFeeE8 = feeConfig.adminNotionalFeeE8;

    uint24 spreadFeeE6 = protocolSpreadFeeE6 + adminSpreadFeeE6;
    uint24 notionalFeeE8 = protocolNotionalFeeE8 + adminNotionalFeeE8;

    PoolImmutables memory immutables = IMetricOmmPool(address(pool)).getImmutables();
    address token0Addr = immutables.token0;
    address token1Addr = immutables.token1;
    uint256 token0Mul = immutables.token0ScaleMultiplier;
    uint256 token1Mul = immutables.token1ScaleMultiplier;

    uint256 surplus0Scaled = (MockERC20(token0Addr).balanceOf(address(pool)) * token0Mul) - totalScaledToken0InBins;
    uint256 surplus1Scaled = (MockERC20(token1Addr).balanceOf(address(pool)) * token1Mul) - totalScaledToken1InBins;

    // Mirror collect fee-split math for scaled amounts (rates passed into collectFees).
    uint256 spread0ToAdmin = spreadFeeE6 == 0 ? 0 : (surplus0Scaled * adminSpreadFeeE6) / spreadFeeE6;
    uint256 spread1ToAdmin = spreadFeeE6 == 0 ? 0 : (surplus1Scaled * adminSpreadFeeE6) / spreadFeeE6;
    uint256 spread0ToProtocol = spreadFeeE6 == 0 ? 0 : (surplus0Scaled * protocolSpreadFeeE6) / spreadFeeE6;
    uint256 spread1ToProtocol = spreadFeeE6 == 0 ? 0 : (surplus1Scaled * protocolSpreadFeeE6) / spreadFeeE6;

    uint256 notional0ToAdmin = notionalFeeE8 == 0 ? 0 : (uint256(notional0) * adminNotionalFeeE8) / notionalFeeE8;
    uint256 notional1ToAdmin = notionalFeeE8 == 0 ? 0 : (uint256(notional1) * adminNotionalFeeE8) / notionalFeeE8;
    uint256 notional0ToProtocol = uint256(notional0) - notional0ToAdmin;
    uint256 notional1ToProtocol = uint256(notional1) - notional1ToAdmin;

    uint256 total0Attempted = spread0ToAdmin + spread0ToProtocol + notional0ToAdmin + notional0ToProtocol;
    uint256 total1Attempted = spread1ToAdmin + spread1ToProtocol + notional1ToAdmin + notional1ToProtocol;

    assertGt(total0Attempted, surplus0Scaled, "token0 attempted payout exceeds computed surplus");
    assertGt(total1Attempted, surplus1Scaled, "token1 attempted payout exceeds computed surplus");
  }
```
