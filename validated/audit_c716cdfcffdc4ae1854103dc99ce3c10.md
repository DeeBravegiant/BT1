### Title
Stale Chainlink Price Accepted Without Validation Enables Inflated rsETH Minting — (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but performs no staleness check, no incomplete-round check, and no zero/negative price check. When Chainlink's oracle is stale or degraded, the function silently returns the last-known (stale) price instead of reverting — the direct on-chain analog of the bridge service's "fallback to default port 8080 when no port is available." A depositor can time a deposit to coincide with a stale inflated price and mint more rsETH than the deposited LST is worth, diluting all existing rsETH holders.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` reads from Chainlink with no validation:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol  line 52-54
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

The five return values of `latestRoundData()` are `(roundId, answer, startedAt, updatedAt, answeredInRound)`. The code discards `roundId`, `updatedAt`, and `answeredInRound` entirely. There is no check that:
- `answeredInRound >= roundId` (stale round detection)
- `updatedAt != 0` (incomplete round detection)
- `price > 0` (invalid/zero price detection)

The sister contract `ChainlinkOracleForRSETHPoolCollateral` — used for L2 pool collateral — implements all three guards:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol  lines 30-32
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

`ChainlinkPriceOracle` is the oracle adapter plugged into `LRTOracle.assetPriceOracle` for L1 LST assets. `LRTOracle.getAssetPrice()` delegates directly to it:

```solidity
// contracts/LRTOracle.sol  line 157
return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
``` [3](#0-2) 

This price is consumed in two critical places:

1. **Deposit minting** — `LRTDepositPool.getRsETHAmountToMint()`:
   ```solidity
   rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
   ``` [4](#0-3) 

2. **rsETH price update** — `LRTOracle._getTotalEthInProtocol()` loops over all supported assets and multiplies each asset's balance by `getAssetPrice(asset)` to compute total protocol TVL, which then sets `rsETHPrice`: [5](#0-4) 

---

### Impact Explanation

**High — Theft of unclaimed yield.**

When Chainlink's feed for an LST (e.g., stETH, rETH) is stale at a price higher than the current market price (e.g., after a depeg event or during sequencer downtime), `getAssetPrice()` silently returns the inflated stale price. A depositor calling `depositAsset()` with that LST receives:

```
rsethAmountToMint = amount × P_stale / rsETHPrice
```

where `P_stale > P_current`. The excess rsETH minted represents value extracted from existing rsETH holders — their proportional claim on the protocol's TVL is diluted. The `minRSETHAmountExpected` slippage guard only protects against receiving *less* than expected; it provides no protection when the attacker *benefits* from an inflated price.

Additionally, if `price` is zero (e.g., Chainlink circuit-breaker scenario), `getRsETHAmountToMint()` returns 0, and a depositor with `minRSETHAmountExpected = 0` loses their deposited LST with no rsETH minted — a temporary fund freeze.

---

### Likelihood Explanation

**Medium.** Chainlink oracles go stale in documented, recurring scenarios: L2 sequencer downtime (Arbitrum, Optimism), extreme network congestion preventing keeper transactions, or Chainlink's own circuit-breaker triggering. The protocol operates on mainnet and multiple L2s. An attacker monitoring oracle freshness can detect a stale round and immediately submit a deposit transaction. No privileged access is required — `depositAsset()` is open to any address.

---

### Recommendation

Apply the same validation pattern already present in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

---

### Proof of Concept

1. Chainlink's stETH/ETH feed goes stale at `P_stale = 1.05e18` (last update before a depeg to `P_current = 0.98e18`).
2. Attacker observes `answeredInRound < roundId` on-chain — the feed is stale but `ChainlinkPriceOracle` will not revert.
3. Attacker calls `LRTDepositPool.depositAsset(stETH, 100e18, 0, "")`.
4. `getRsETHAmountToMint()` computes: `100e18 × 1.05e18 / rsETHPrice` instead of `100e18 × 0.98e18 / rsETHPrice`.
5. Attacker receives ~7.1% more rsETH than the deposited stETH is worth at current market price.
6. All existing rsETH holders' redemption value is diluted by the excess rsETH minted.
7. Attacker immediately redeems or sells the excess rsETH, extracting yield from existing holders.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L30-32)
```text
        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L336-343)
```text
        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
