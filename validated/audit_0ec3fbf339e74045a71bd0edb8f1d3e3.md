### Title
`RSETHPriceFeed.latestRoundData()` Returns Stale rsETH/USD Price with a Misleading Fresh `updatedAt` Timestamp — (File: `contracts/oracles/RSETHPriceFeed.sol`)

---

### Summary

`RSETHPriceFeed` is a Chainlink-compatible price feed that computes rsETH/USD by multiplying the live ETH/USD Chainlink answer by the stored `LRTOracle.rsETHPrice`. The `updatedAt` timestamp it returns, however, is sourced exclusively from the ETH/USD Chainlink feed — not from when `rsETHPrice` was last written to storage. Because `rsETHPrice` is a manually-triggered cached value that can remain frozen (most critically when the protocol auto-pauses on a large price drop), any external protocol that relies on `updatedAt` to gate staleness will be deceived into treating an outdated rsETH/USD price as fresh.

---

### Finding Description

**Step 1 — `rsETHPrice` is a stored, manually-updated value.**

`LRTOracle.rsETHPrice` is a state variable written only when `_updateRsETHPrice()` executes to completion. [1](#0-0) [2](#0-1) 

**Step 2 — A significant price drop causes `_updateRsETHPrice()` to pause the protocol and return *without* updating `rsETHPrice`.**

When `newRsETHPrice` falls below `highestRsethPrice` by more than `pricePercentageLimit`, the function pauses the deposit pool, the withdrawal manager, and the oracle itself, then returns early. `rsETHPrice` is never reassigned in this branch, so it remains at the last (higher) value indefinitely. [3](#0-2) [4](#0-3) 

**Step 3 — `RSETHPriceFeed.latestRoundData()` returns `updatedAt` from the ETH/USD Chainlink feed, not from when `rsETHPrice` was last written.**

The function delegates entirely to `ETH_TO_USD.latestRoundData()` for all metadata fields (`roundId`, `startedAt`, `updatedAt`, `answeredInRound`) and only replaces `answer` with the product of the ETH/USD price and the stale `rsETHPrice`. [5](#0-4) 

The ETH/USD Chainlink feed is updated every ~1 hour (heartbeat). Its `updatedAt` will therefore always appear fresh, even when `rsETHPrice` has not been updated for days.

**Step 4 — External protocols trust `updatedAt` to gate staleness.**

Protocols such as Morpho (referenced in the README as a consumer of `RSETHPriceFeed`) implement a standard Chainlink staleness check: if `block.timestamp - updatedAt > threshold`, reject the price. Because `updatedAt` reflects the ETH/USD feed's freshness, this check passes even when the rsETH/ETH component is frozen at a pre-slashing value.

---

### Impact Explanation

When a slashing event or other loss causes the true rsETH/ETH rate to drop sharply, `_updateRsETHPrice()` pauses the LRT-rsETH protocol and leaves `rsETHPrice` at the pre-loss value. `RSETHPriceFeed` continues to serve this inflated price with a fresh `updatedAt`. An rsETH holder can then:

1. Supply rsETH as collateral to Morpho (or any integrated lending protocol) at the stale, inflated price.
2. Borrow the maximum allowed against that collateral.
3. Walk away, leaving the lending protocol with under-collateralised debt.

This constitutes direct theft of funds from the lending protocol's liquidity providers, enabled by the stale-but-appearing-fresh price feed. The impact maps to **Critical — direct theft of user funds in motion through a supported integration path**.

---

### Likelihood Explanation

EigenLayer slashing is a live, protocol-acknowledged risk (the `pricePercentageLimit` guard exists precisely because of it). A single significant slashing event on a delegated operator is sufficient to trigger the auto-pause path and freeze `rsETHPrice`. The attack requires no privileged access: any rsETH holder can supply collateral to Morpho and borrow against the stale price. Likelihood is **Medium** — the trigger (a large slashing event) is uncommon but realistic and has occurred in analogous protocols.

---

### Recommendation

1. **Track the rsETH price update timestamp.** Add a `uint256 public rsETHPriceUpdatedAt` variable to `LRTOracle` and set it to `block.timestamp` every time `rsETHPrice` is written.

2. **Return the correct `updatedAt` in `RSETHPriceFeed`.** Replace the `updatedAt` from the ETH/USD feed with `min(ethToUsdUpdatedAt, rsETHPriceUpdatedAt)` so that consumers see the true age of the composite price.

3. **Add an explicit staleness revert.** Inside `RSETHPriceFeed.latestRoundData()`, revert if `block.timestamp - rsETHPriceUpdatedAt` exceeds a configurable maximum age (e.g., 24 hours), consistent with the heartbeat of the underlying oracle.

---

### Proof of Concept

```
Precondition: pricePercentageLimit = 2e16 (2%), rsETHPrice = 1.05e18, highestRsethPrice = 1.05e18.

1. EigenLayer slashing reduces protocol TVL by 3%.
2. Anyone calls LRTOracle.updateRSETHPrice().
   → newRsETHPrice ≈ 1.0185e18 (−3% from peak).
   → diff = 1.05e18 − 1.0185e18 = 0.0315e18 > 2% × 1.05e18 = 0.021e18.
   → isPriceDecreaseOffLimit = true.
   → Protocol pauses; function returns WITHOUT writing rsETHPrice.
   → rsETHPrice remains 1.05e18.

3. ETH/USD Chainlink feed updates normally (e.g., updatedAt = block.timestamp − 10 minutes).

4. Attacker calls RSETHPriceFeed.latestRoundData().
   → updatedAt = ETH/USD updatedAt (10 minutes ago — appears fresh).
   → answer = 1.05e18 × ETH_USD_price / 1e18  (inflated by ~3%).

5. Attacker supplies rsETH to Morpho; Morpho reads RSETHPriceFeed, sees fresh updatedAt,
   accepts the inflated price, and allows maximum borrowing.

6. Attacker borrows against the over-valued collateral and exits.
   True collateral value is ~3% lower; Morpho accrues bad debt.
``` [5](#0-4) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L270-282)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/oracles/RSETHPriceFeed.sol (L63-70)
```text
    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
        answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
    }
```
