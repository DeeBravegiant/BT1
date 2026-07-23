The code is confirmed. Let me verify the exact lines cited in the claim against the actual source.

Audit Report

## Title
Synthetic Ratio Integer Truncation to Zero in `_getBidAndAskPrice()` Permanently Blocks All Pool Swaps — (`smart-contracts-poc/contracts/AnchoredPriceProvider.sol`)

## Summary
In `AnchoredPriceProvider._getBidAndAskPrice()`, the synthetic ratio computation `Math.mulDiv(mid, ORACLE_DECIMALS, mid2)` at line 267 truncates to zero whenever the base feed price is less than `1e-8` of the quote feed price. No zero-check follows the division, so `_computeBidAsk(0, spreadBps)` is called, which returns the stall sentinel `(0, type(uint128).max)`. `getBidAndAskPrice()` then reverts with `FeedStalled`, and the pool re-throws as `PriceProviderFailed` on every `swap()` call, completely blocking swaps for as long as the price ratio remains extreme.

## Finding Description
In the two-feed synthetic ratio path, `_getBidAndAskPrice()` computes:

```solidity
if (_quote != bytes32(0)) {
    (uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote);
    if (!ok2 || mid2 == 0) return (0, type(uint128).max);
    mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);   // line 267
    spreadBps += spreadBps2;
}
return _computeBidAsk(mid, spreadBps);               // line 271
``` [1](#0-0) 

`ORACLE_DECIMALS = 1e8`. When `mid * 1e8 < mid2`, integer division floors the result to zero. There is no guard for `mid == 0` between line 267 and line 271.

`_computeBidAsk(0, spreadBps)` then calls `_bandEdge(0, BPS_BASE_U - half, Floor)`, which evaluates to `Math.mulDiv(0, Q64 * edgeFactor, STEP_DENOM, Floor) = 0`: [2](#0-1) 

This makes `refBid == 0`, triggering the stall sentinel return:

```solidity
if (refBid == 0 || refAsk > type(uint128).max || refBid >= refAsk) {
    return (0, type(uint128).max);
}
``` [3](#0-2) 

`getBidAndAskPrice()` then reverts: [4](#0-3) 

The pool's `_getBidAndAskPriceX64()` catches and re-throws as `PriceProviderFailed`: [5](#0-4) 

`swap()` calls `_getBidAndAskPriceX64()` as its very first action after the reentrancy guard: [6](#0-5) 

Every swap reverts for as long as the price ratio remains below `1e-8`. The existing guard at line 265 (`if (!ok2 || mid2 == 0)`) only protects against a zero denominator, not against a zero quotient after division. [7](#0-6) 

## Impact Explanation
All swaps on any pool using `AnchoredPriceProvider` in synthetic ratio mode are completely blocked whenever the base/quote price ratio falls below `1e-8`. This is broken core pool functionality: the primary user-facing action (swap) becomes permanently unusable until the price ratio recovers. LPs cannot rebalance through swaps and traders cannot execute, satisfying the "Broken core pool functionality causing unusable swap flows" impact gate.

## Likelihood Explanation
The condition `mid * 1e8 < mid2` is reachable through normal market price movement without any privileged action. A base token priced at $0.00001 (e.g., SHIB, `mid = 1000` in 8-decimal oracle units) paired against BTC at $60,000 (`mid2 = 6_000_000_000_000`) yields `Math.mulDiv(1000, 1e8, 6e12) = 0`. More critically, any token that crashes by more than 8 orders of magnitude relative to its quote token triggers the same path. The synthetic ratio mode is an explicitly supported and documented feature (BTC/USD ÷ ETH/USD = BTC/ETH), and no attacker input is required — the condition arises from market prices alone.

## Recommendation
Add a zero-check for the synthetic ratio result immediately after the division at line 267:

```solidity
mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
if (mid == 0) return (0, type(uint128).max); // ratio underflows 8-decimal precision
```

Alternatively, scale the intermediate precision before dividing (e.g., use `1e18` instead of `1e8`) and adjust `_computeBidAsk` and `_bandEdge` accordingly to preserve sub-`1e-8` ratios.

## Proof of Concept
Deploy the following contract and call `demonstrateZeroRatio()`:

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";

contract PoC {
    uint256 constant ORACLE_DECIMALS = 1e8;

    function demonstrateZeroRatio() external pure returns (uint256 syntheticMid) {
        uint256 mid  = 1_000;               // SHIB at $0.00001 → 8-decimal oracle price
        uint256 mid2 = 6_000_000_000_000;   // BTC  at $60,000  → 8-decimal oracle price
        syntheticMid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
        // Returns 0: 1000 * 1e8 / 6e12 = 1e11 / 6e12 = 0
        // → _computeBidAsk(0, spreadBps) → (0, type(uint128).max)
        // → getBidAndAskPrice() reverts FeedStalled
        // → swap() reverts PriceProviderFailed
    }
}
```

A Foundry integration test can fork the production `AnchoredPriceProvider` with a mock oracle returning `mid=1000` for the base feed and `mid2=6_000_000_000_000` for the quote feed, then call `pool.swap(...)` and assert it reverts with `PriceProviderFailed`.

### Citations

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L214-217)
```text
    function getBidAndAskPrice() external override returns (uint128 bid, uint128 ask) {
        (bid, ask) = _getBidAndAskPrice();
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L244-250)
```text
    function _bandEdge(
        uint256       mid,
        uint256       edgeFactor,
        Math.Rounding rounding
    ) internal pure returns (uint256) {
        return Math.mulDiv(mid, Q64 * edgeFactor, STEP_DENOM, rounding);
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L263-271)
```text
        if (_quote != bytes32(0)) {
            (uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote);
            if (!ok2 || mid2 == 0) return (0, type(uint128).max);
            // Synthetic ratio (8-decimal): mid1 / mid2. Relative uncertainties of a ratio add.
            mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
            spreadBps += spreadBps2;
        }

        return _computeBidAsk(mid, spreadBps);
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L311-313)
```text
        if (refBid == 0 || refAsk > type(uint128).max || refBid >= refAsk) {
            return (0, type(uint128).max);
        }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L227-228)
```text
    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();
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
