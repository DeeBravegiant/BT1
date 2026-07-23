Looking at the actual code to verify each step of the claim.

The code path is fully verified. All claims check out against the actual source:

- Line 267: `mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2)` — no zero-check follows
- `_readLeg` guarantees both `mid > 0` and `mid2 > 0` before the division (lines 265, 287), so zero can only arise from the division itself
- `_computeBidAsk(0, spreadBps)`: `_bandEdge(0, ...)` = 0, so `refBid == 0` triggers sentinel return at line 311
- `getBidAndAskPrice()` reverts `FeedStalled` at line 216
- `swap()` calls `_getBidAndAskPriceX64()` at line 228 as its first action, which catches and re-throws as `PriceProviderFailed`

---

Audit Report

## Title
Synthetic Ratio Precision Loss in `_getBidAndAskPrice()` Silently Produces Zero Mid, Reverting All Swaps — (`smart-contracts-poc/contracts/AnchoredPriceProvider.sol`)

## Summary
In synthetic two-feed mode (`quoteFeedId != bytes32(0)`), `AnchoredPriceProvider._getBidAndAskPrice()` computes the ratio `mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2)` with `ORACLE_DECIMALS = 1e8`. When `mid * 1e8 < mid2`, integer division floors the result to zero. No post-division zero-check exists, so `_computeBidAsk(0, spreadBps)` is called, which returns the stall sentinel `(0, type(uint128).max)`. `getBidAndAskPrice()` then reverts with `FeedStalled`, and the pool re-throws as `PriceProviderFailed`, blocking every swap.

## Finding Description
`_readLeg` guarantees both legs are non-zero before the division: the base leg is checked at line 287 (`if (mid == 0 ...) return ... false`) and the quote leg is checked at line 265 (`if (!ok2 || mid2 == 0) return (0, type(uint128).max)`). The only path to a zero `mid` is through the division itself at line 267:

```solidity
mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);  // line 267 — no zero-check after
spreadBps += spreadBps2;
// falls through to:
return _computeBidAsk(mid, spreadBps);           // line 271
```

Inside `_computeBidAsk` with `mid = 0`, `_bandEdge(0, edgeFactor, rounding)` evaluates `Math.mulDiv(0, Q64 * edgeFactor, STEP_DENOM, rounding) = 0`, so `refBid = 0`. The guard at line 311 fires immediately:

```solidity
if (refBid == 0 || refAsk > type(uint128).max || refBid >= refAsk) {
    return (0, type(uint128).max);
}
```

`getBidAndAskPrice()` receives `(0, type(uint128).max)` and reverts:

```solidity
if (bid == 0 || ask == type(uint128).max) revert FeedStalled();  // line 216
```

`_getBidAndAskPriceX64()` catches this and re-throws:

```solidity
} catch (bytes memory reason) {
    revert PriceProviderFailed(reason);  // line 811
}
```

`swap()` calls `_getBidAndAskPriceX64()` at line 228 as its very first action, so every swap reverts for as long as the price ratio remains below `1e-8`.

## Impact Explanation
All swaps on any pool using `AnchoredPriceProvider` in synthetic ratio mode are completely blocked whenever the base/quote price ratio falls below `1e-8` in 8-decimal oracle units. This is broken core pool functionality: the primary user-facing action (swap) becomes entirely unusable. LPs cannot rebalance through swaps and traders cannot execute. This satisfies the "Broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows" impact gate.

## Likelihood Explanation
The condition `mid * 1e8 < mid2` is reachable with realistic, correctly-reported oracle prices. Example: base = SHIB at $0.00001 → `mid = 1_000`; quote = BTC at $60,000 → `mid2 = 6_000_000_000_000`. Then `Math.mulDiv(1_000, 1e8, 6e12) = 1e11 / 6e12 = 0`. Any pool in synthetic ratio mode pairing a low-unit-price token against a high-unit-price token is vulnerable. No privileged action is required; the condition arises from normal market prices being correctly reported by the oracle.

## Recommendation
Add a zero-check immediately after the division at line 267:

```solidity
mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
if (mid == 0) return (0, type(uint128).max); // ratio underflows 8-decimal precision
```

This fails closed (same behavior as a stale feed) rather than silently passing a zero mid into `_computeBidAsk`. Alternatively, scale `mid` by a higher-precision factor before dividing to preserve sub-`1e-8` ratios, adjusting `_bandEdge`'s `STEP_DENOM` accordingly.

## Proof of Concept
Deploy a pool with `AnchoredPriceProvider` in synthetic ratio mode where the base oracle reports `mid = 1_000` (e.g., SHIB at $0.00001) and the quote oracle reports `mid2 = 6_000_000_000_000` (e.g., BTC at $60,000). Call `swap()` on the pool. The call reverts with `PriceProviderFailed` wrapping `FeedStalled`. Foundry unit test:

```solidity
// Set up mock oracle: baseFeed returns (1_000, validSpread, ...), quoteFeed returns (6_000_000_000_000, validSpread, ...)
// Deploy AnchoredPriceProvider with quoteFeedId set
// Call pool.swap(...) → reverts PriceProviderFailed(FeedStalled)
uint256 syntheticMid = Math.mulDiv(1_000, 1e8, 6_000_000_000_000); // == 0
assertEq(syntheticMid, 0);
```