### Title
Stale Chainlink Price Consumed Without Validation Enables Incorrect rsETH Minting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards every validation field (`roundId`, `updatedAt`, `answeredInRound`). A stale or incomplete Chainlink round is consumed as if it were a valid, finalized price — the exact same class of error as using a VRF `requestId` before `fulfillRandomWords()` has been called. The price is fed directly into rsETH minting math, making every public deposit path vulnerable.

### Finding Description
`ChainlinkPriceOracle.getAssetPrice()` reads the Chainlink feed with:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values are available but four are thrown away. No check is made for:
- `answeredInRound < roundId` — round not yet answered (stale)
- `updatedAt == 0` — incomplete round
- `price <= 0` — invalid or negative price
- `block.timestamp - updatedAt > heartbeat` — data too old

The same repository already implements all of these checks in `ChainlinkOracleForRSETHPoolCollateral.getRate()`:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The unchecked price flows through:

1. `LRTOracle.getAssetPrice(asset)` → `LRTDepositPool.getRsETHAmountToMint()` → `_beforeDeposit()` → `depositETH()` / `depositAsset()` (both public, unprivileged)
2. `LRTOracle._getTotalEthInProtocol()` → `_updateRsETHPrice()` (public)

### Impact Explanation
**High — Theft of unclaimed yield / share dilution of existing rsETH holders.**

If a Chainlink feed returns a stale, inflated price for an LST (e.g., the LST has dropped in value but the oracle has not updated), a depositor calling `depositAsset()` receives rsETH calculated at the old higher price:

```
rsethAmountToMint = (depositAmount * staleHighPrice) / rsETHPrice
```

The depositor receives more rsETH than the deposited assets are worth, diluting every existing rsETH holder's share of the underlying pool — a direct theft of accrued yield. Additionally, if `price` is negative (possible in a broken/incomplete round), `uint256(int256(negative))` wraps to an astronomically large value, causing unbounded rsETH minting and protocol insolvency.

### Likelihood Explanation
**Medium.** Chainlink oracles return stale data during network congestion, sequencer downtime (relevant for any L2 deployment), or when the deviation threshold and heartbeat have not triggered. This is a well-documented, recurring real-world condition. The attack requires no privileged access — any depositor can call `depositAsset()` or `depositETH()` at the moment a stale price is live.

### Recommendation
Apply the same validation pattern already present in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

if (answeredInRound < roundId) revert StalePrice();
if (updatedAt == 0) revert IncompleteRound();
if (price <= 0) revert InvalidPrice();
if (block.timestamp - updatedAt > STALENESS_THRESHOLD) revert StalePrice();
```

### Proof of Concept

1. Chainlink's stETH/ETH feed enters a stale round (e.g., sequencer downtime). `updatedAt` is 2 hours old; the real stETH price has dropped 5% but the feed still shows the old price.
2. Attacker calls `LRTDepositPool.depositAsset(stETH, amount, 0, "")`.
3. `_beforeDeposit` → `getRsETHAmountToMint` → `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)`.
4. `latestRoundData()` returns the stale inflated price; no revert occurs because `updatedAt`, `answeredInRound`, and `price` are never checked.
5. `rsethAmountToMint = (amount * stalePrice) / rsETHPrice` — attacker receives ~5% more rsETH than the deposited stETH is worth.
6. Attacker immediately redeems rsETH, extracting value from existing holders.

---

**Root cause lines:** [1](#0-0) 

**Contrast — correct validation in the same repo:** [2](#0-1) 

**Downstream minting path (unprivileged entry):** [3](#0-2) 

**rsETH price update path (public):** [4](#0-3)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L27-36)
```text
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L336-344)
```text
        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

```
