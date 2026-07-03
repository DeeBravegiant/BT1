Audit Report

## Title
Missing Chainlink `latestRoundData()` Staleness and Validity Checks in `ChainlinkPriceOracle.getAssetPrice` - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice` silently discards `updatedAt`, `answeredInRound`, and the sign of `answer` returned by `latestRoundData()`. A stale or negative Chainlink price propagates unchecked into `LRTOracle._updateRsETHPrice()`, corrupting the stored `rsETHPrice`. Because `updateRSETHPrice()` is permissionless and the mint calculation divides by the stored `rsETHPrice`, an attacker can exploit a deflated `rsETHPrice` (caused by a stale low price for one asset) by depositing a *different* correctly-priced asset to receive more rsETH than their deposit is worth, draining value from existing holders.

## Finding Description

**Root cause — no validation in `ChainlinkPriceOracle.getAssetPrice`:**

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L52-54
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All four validity fields (`roundId`, `startedAt`, `updatedAt`, `answeredInRound`) are discarded. A negative `price` is cast directly to `uint256`, wrapping to near-`type(uint256).max`. The protocol's own newer wrapper `ChainlinkOracleForRSETHPoolCollateral` (L30-32) applies exactly the missing checks (`answeredInRound < roundID`, `timestamp == 0`, `ethPrice <= 0`), confirming the protocol recognises these as necessary.

**Permissionless price update:**

```solidity
// contracts/LRTOracle.sol L87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

Any unprivileged caller can trigger a price update at any time.

**Mint calculation uses live `getAssetPrice` and stored `rsETHPrice`:**

```solidity
// contracts/LRTDepositPool.sol L520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

The numerator uses the live oracle price for the deposited asset; the denominator uses the stored `rsETHPrice`. These two values are computed from different oracle calls at different times, creating an exploitable asymmetry.

**Exploit path (multi-asset pool):**

Suppose the pool holds stETH (true price 1.0 ETH) and ETHx (true price 1.0 ETH) in equal proportion, with `rsETHPrice = 1.0` and `pricePercentageLimit = 0` (default uninitialized value).

1. The Chainlink stETH/ETH feed goes stale and returns 0.9 ETH (10% below true).
2. Attacker calls `updateRSETHPrice()`. `_getTotalEthInProtocol()` calls `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns 0.9. With a 50/50 pool, `totalETHInProtocol` is understated by ~5%, so `rsETHPrice` is written as ~0.95.
3. Attacker deposits ETHx (whose Chainlink feed is current and returns 1.0). Mint calculation: `(amount × 1.0) / 0.95 ≈ amount × 1.053`. The attacker receives ~5.3% more rsETH than their deposit is worth.
4. After the stETH feed recovers, `rsETHPrice` corrects upward. The attacker redeems rsETH at the true price, extracting value from existing holders.

**Why existing guards are insufficient:**

The `pricePercentageLimit` downside check in `_updateRsETHPrice` (L270-281) pauses the protocol only when the price decrease exceeds the configured limit. When `pricePercentageLimit == 0` (the default uninitialized state), the condition `pricePercentageLimit > 0 && ...` is always false and the guard never fires. Even when set, small-to-moderate stale deviations within the limit pass through unchecked. The upside guard (L252-266) similarly requires `pricePercentageLimit > 0` to have any effect.

## Impact Explanation

**Critical — direct theft of user funds / protocol insolvency.**

When `pricePercentageLimit` is zero or the stale deviation falls within the configured limit, an attacker can mint rsETH at a deflated price by depositing a correctly-priced asset immediately after locking in a corrupted `rsETHPrice`. Repeated exploitation drains the backing pool, causing insolvency for existing rsETH holders. The inverse (inflated stale price) causes depositors to receive fewer rsETH tokens than their deposit is worth — direct loss of depositor value.

## Likelihood Explanation

**Medium.** Chainlink feeds do go stale during network congestion, L2 sequencer downtime, or oracle node failures. `updateRSETHPrice()` is permissionless, so no privileged actor needs to be involved. `pricePercentageLimit` defaults to 0 and must be explicitly configured by an admin; any deployment window where it is unset is fully exposed. The protocol holds significant TVL in stETH and ETHx, both routed through `ChainlinkPriceOracle`.

## Recommendation

Apply the same validation pattern already present in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    // Recommended: if (block.timestamp - updatedAt > STALENESS_THRESHOLD) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Additionally, ensure `pricePercentageLimit` is set to a non-zero value at deployment and cannot remain at 0 indefinitely.

## Proof of Concept

**Setup (Foundry fork test):**
- Fork mainnet with a live stETH/ETH and ETHx/ETH Chainlink feed.
- Record `rsETHPrice` and `highestRsethPrice` (both 1.0 ETH). Confirm `pricePercentageLimit == 0`.

**Step 1 — Simulate stale stETH feed:**
- Mock `AggregatorV3Interface(stETH_feed).latestRoundData()` to return `(roundId=N, answer=0.9e8, startedAt=T-48h, updatedAt=T-48h, answeredInRound=N-1)` (stale, 10% below true, incomplete round).

**Step 2 — Corrupt rsETHPrice:**
- Call `LRTOracle.updateRSETHPrice()` as an unprivileged EOA.
- Assert `rsETHPrice < 1.0 ether` (deflated by ~5% in a 50/50 pool).
- Assert protocol is NOT paused (because `pricePercentageLimit == 0`).

**Step 3 — Exploit deposit:**
- Attacker approves and calls `LRTDepositPool.depositAsset(ETHx, 100e18, 0, "")`.
- Record `rsethMinted`. Assert `rsethMinted > 100e18` (more than 1:1 at true price).

**Step 4 — Price recovery and redemption:**
- Restore the stETH feed to true price. Call `updateRSETHPrice()`.
- Assert `rsETHPrice` returns to ~1.0 ether.
- Attacker redeems rsETH; assert net ETH received > 100 ETH deposited.

**Expected result:** The attacker extracts value from existing rsETH holders proportional to the stale price deviation and the stETH share of pool TVL, with no revert at any step.