### Title
Oracle Spread Completely Ignored When `confidenceParam` Is Zero (Default State), Allowing Swaps at Unclamped Prices During High Oracle Uncertainty — (File: `smart-contracts-poc/contracts/PriceProviderL2.sol`, `smart-contracts-poc/contracts/ProtectedPriceProvider.sol`, `smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol`)

---

### Summary

All three price provider contracts (`PriceProvider`, `PriceProviderL2`, `ProtectedPriceProvider`, `ProtectedPriceProviderL2`) store `confidenceParam` as a plain storage variable that defaults to `0`. When `confidenceParam == 0`, the oracle's reported spread (its confidence interval / uncertainty measure) is multiplied by zero and completely discarded. The pool's bid/ask is then derived solely from the fixed `marginStep`, regardless of how wide the oracle's uncertainty band is. There is no constructor argument, no initialization guard, and no check in the swap path that prevents execution when `confidenceParam == 0`. This is the direct Metric OMM analog of the Pyth confidence-interval-ignored finding.

---

### Finding Description

In every price provider variant the bid/ask computation follows this pattern:

```solidity
// PriceProviderL2.sol ~L233
uint256 adjustedSpread = spread * confidenceParam;   // ← zero when confidenceParam == 0
(uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);
``` [1](#0-0) 

`confidenceParam` is declared as a plain storage slot:

```solidity
uint256 public confidenceParam;
uint256 public lastConfidenceUpdate;
``` [2](#0-1) 

Its default Solidity value is `0`. The constructor of every provider variant accepts no `confidenceParam` argument and performs no initialization of this field. The only way to set it is via `setConfidenceParam()`, which is callable only by the factory after deployment, and is subject to a 1-minute cooldown:

```solidity
uint256 public constant CONFIDENCE_COOLDOWN = 1 minutes;
``` [3](#0-2) 

The protocol's own test suite explicitly documents and validates the zero-confidence state as producing a valid (non-reverting) bid/ask:

```solidity
// testConfidenceZeroMeansNoSpread
// confidenceParam = 0 (default) → adjustedSpread = 0 → only marginStep
(uint128 bid, uint128 ask) = provider.getBidAndAskPrice();
assertGt(bid, 0, "bid should be non-zero (marginStep provides separation)");
assertLt(bid, ask, "bid < ask from marginStep alone");
``` [4](#0-3) 

This means a freshly deployed price provider — before the factory has called `setConfidenceParam` — silently ignores the oracle's entire uncertainty band and quotes a fixed `marginStep`-only spread to the pool. No swap-path guard checks `confidenceParam != 0` before accepting the returned bid/ask.

The existing validity checks (staleness, price guard, bid < ask) do **not** catch this condition:

```solidity
// 1. Staleness check
if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) { ... }
// 2. Basic validity — price must be positive, spread must not be stalled marker
if (mid == 0 || spread >= ORACLE_BPS) { ... }
// 3. Price guard check
if (mid < guardMin || mid > guardMax) { ... }
// 5. Compute bid/ask from mid + confidence-adjusted spread
uint256 adjustedSpread = spread * confidenceParam;   // silently 0
``` [5](#0-4) 

---

### Impact Explanation

When the oracle reports a large spread (e.g., 500 bps uncertainty during a volatile event) but `confidenceParam == 0`, the pool quotes bid/ask at mid ± `marginStep` only (e.g., ±10 bps). A trader who observes that the true market price is near the edge of the oracle's uncertainty band can:

1. Buy the underpriced token from the pool at `ask = mid * (1 + marginStep)` when the real ask should be `mid * (1 + 500 bps)`.
2. Immediately sell at the real market price, extracting the 490 bps gap from LP reserves.

LPs suffer a direct loss of principal proportional to the gap between the oracle's actual uncertainty and the `marginStep`-only spread. This is a swap conservation failure: the pool receives the correct input but pays out more than the oracle-anchored price permits, draining LP balances.

---

### Likelihood Explanation

- Every newly deployed price provider starts with `confidenceParam == 0`. There is no deployment-time enforcement.
- The factory must make a separate post-deployment transaction to set `confidenceParam`, and the 1-minute cooldown means it cannot be corrected instantly if the oracle suddenly widens its spread.
- Oracle uncertainty spikes are common during market stress — exactly when the gap between `marginStep`-only spread and the true uncertainty band is largest and most exploitable.
- Any unprivileged user can call `swap` against the pool at any time; no special role is required to trigger the exploit.

---

### Recommendation

1. **Require non-zero `confidenceParam` at construction**: Add a constructor parameter for `confidenceParam` and validate `confidenceParam > 0` (or enforce a minimum meaningful value such as `CONFIDENCE_BASE`).
2. **Guard the swap path**: In `_computeBidAsk` / `_getBidAndAskPrice`, revert (or return the stalled sentinel `(0, type(uint128).max)`) when `confidenceParam == 0`, preventing swaps until the factory has explicitly configured the confidence multiplier.
3. **Remove the cooldown floor at zero**: The 1-minute cooldown prevents rapid adjustment; if `confidenceParam` must be raised urgently (oracle uncertainty spike), the cooldown creates a forced exposure window. Consider allowing upward adjustments without cooldown, or reducing the cooldown for emergency increases.

---

### Proof of Concept

```solidity
// 1. Factory deploys PriceProviderL2 — confidenceParam defaults to 0.
PriceProviderL2 provider = new PriceProviderL2(
    factory, address(oracle), FEED_ID,
    marginStep, MAX_TIME_DELTA, FUTURE_TOLERANCE,
    BASE_TOKEN, QUOTE_TOKEN
);
// confidenceParam == 0 at this point; factory has NOT called setConfidenceParam yet.

// 2. Oracle reports a price with 500 bps spread (high uncertainty event).
oracle.setFeed(FEED_ID, 100_000_000 /*mid*/, 500 /*spread bps*/, block.timestamp);

// 3. Provider returns bid/ask ignoring the 500 bps spread entirely.
(uint128 bid, uint128 ask) = provider.getBidAndAskPrice();
// bid ≈ mid * (1 - marginStep), ask ≈ mid * (1 + marginStep)  — only ~10 bps apart
// Expected with confidence: bid ≈ mid * (1 - 510 bps), ask ≈ mid * (1 + 510 bps)

// 4. Attacker swaps against the pool, buying token0 at ask = mid*(1+10bps)
//    while the real market ask is mid*(1+510bps).
// 5. Attacker sells on external market, capturing ~500 bps profit from LP reserves.
```

The test `testConfidenceZeroMeansNoSpread` in `smart-contracts-poc/test/PriceProviderL2.t.sol` already demonstrates step 3 — the pool returns a valid, non-reverting bid/ask with the oracle spread fully discarded. [6](#0-5)

### Citations

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L214-234)
```text
        // 2. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
            return (0, type(uint128).max);
        }

        // 3. Basic validity — price must be positive, spread must not be stalled marker
        if (mid == 0 || spread >= ORACLE_BPS) {
            return (0, type(uint128).max);
        }

        // 4. Price guard check (moved from oracle)
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(offchainFeedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) {
            return (0, type(uint128).max);
        }

        // 5. Compute bid/ask from mid + confidence-adjusted spread
        //    confidenceParam multiplies oracle spread; 0 means no spread
        uint256 adjustedSpread = spread * confidenceParam;
        (uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);
```

**File:** smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol (L19-19)
```text
    uint256 public constant CONFIDENCE_COOLDOWN = 1 minutes;
```

**File:** smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol (L48-49)
```text
    uint256 public confidenceParam;
    uint256 public lastConfidenceUpdate;
```

**File:** smart-contracts-poc/test/PriceProviderL2.t.sol (L255-268)
```text
    function testConfidenceZeroMeansNoSpread() public {
        vm.warp(100);
        offchain.setFeed(FEED_ID, 100_000_000, 500, 100);

        // confidenceParam = 0 (default) → adjustedSpread = 0 → only marginStep
        (uint128 bid, uint128 ask) = provider.getBidAndAskPrice();
        assertGt(bid, 0, "bid should be non-zero (marginStep provides separation)");
        assertLt(bid, ask, "bid < ask from marginStep alone");

        uint256 expectedBid = Math.mulDiv(uint256(100_000_000), Q64 * STEP_BID_FACTOR, STEP_DENOM);
        uint256 expectedAsk = Math.mulDiv(uint256(100_000_000), Q64 * STEP_ASK_FACTOR, STEP_DENOM, Math.Rounding.Ceil);
        assertEq(bid, expectedBid, "bid should be mid with only marginStep applied");
        assertEq(ask, expectedAsk, "ask should be mid with only marginStep applied");
    }
```
