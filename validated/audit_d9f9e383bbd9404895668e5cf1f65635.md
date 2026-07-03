### Title
Missing Chainlink Price Feed Staleness Validation Allows Stale Price Exploitation - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards every return value except `price`, performing zero staleness or validity checks. A stale Chainlink price can be used to mint excess rsETH on deposit or receive excess assets on withdrawal, at the expense of other protocol participants.

### Finding Description
`ChainlinkPriceOracle.getAssetPrice()` is the primary price oracle for all supported LST assets in the LRT protocol. Its implementation is:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol line 52
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values from `latestRoundData()` — `roundId`, `answer`, `startedAt`, `updatedAt`, `answeredInRound` — are available, but only `answer` (the price) is used. The contract never checks:

- `answeredInRound >= roundId` — detects a stale round
- `updatedAt != 0` — detects an incomplete round
- `price > 0` — detects an invalid/negative price
- `block.timestamp - updatedAt <= heartbeat` — detects a price that has not been refreshed within the feed's expected update window

This is the direct analog of the VeloOracle bug: just as VeloOracle assumed a past observation timestamp meant the spot price was safe (ignoring the 15-second update window), `ChainlinkPriceOracle` assumes `latestRoundData()` always returns a current, valid price (ignoring the feed's heartbeat and round-completion state).

The contrast with the protocol's own `ChainlinkOracleForRSETHPoolCollateral.getRate()` makes the omission clear — that contract, used for pool collateral pricing, explicitly checks all three conditions:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol lines 30-32
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

`ChainlinkPriceOracle.getAssetPrice()` is consumed by `LRTOracle.getAssetPrice()`, which is called in two critical paths:

1. **Deposit minting** — `LRTDepositPool.getRsETHAmountToMint()` uses `lrtOracle.getAssetPrice(asset)` and `lrtOracle.rsETHPrice()` to compute how many rsETH tokens to mint per deposited LST.
2. **Withdrawal payout** — `LRTWithdrawalManager._createUnlockParams()` reads `lrtOracle.getAssetPrice(asset)` to compute the asset payout for a withdrawal request.

### Impact Explanation
**Impact: Critical — Direct theft of user funds.**

If a Chainlink feed for a supported LST (e.g., stETH/ETH, rETH/ETH) becomes stale at a price higher than the true market price, an attacker can:

1. Deposit the LST while the stale inflated price is active → `getRsETHAmountToMint()` returns a larger rsETH amount than the deposit is worth.
2. Receive excess rsETH, which represents a claim on more ETH than was deposited.
3. Immediately redeem or sell the excess rsETH, extracting value from existing depositors.

The dilution is permanent: the rsETH supply is inflated relative to the protocol's actual ETH backing, reducing the redemption value for all other holders. This constitutes direct theft of at-rest user funds.

### Likelihood Explanation
**Likelihood: Medium.**

Chainlink LST/ETH feeds on Ethereum mainnet have 24-hour heartbeats and 0.5% deviation thresholds. Staleness can occur during:
- Prolonged network congestion preventing oracle keeper transactions
- Chainlink node outages or feed deprecation
- L2 sequencer downtime (relevant if the protocol is deployed on L2 chains, which the `contracts/L2/` directory indicates)

The window of exploitation is bounded by the staleness duration, but no on-chain mechanism prevents a deposit during that window. The `pricePercentageLimit` guard in `LRTOracle._updateRsETHPrice()` only fires when the manager calls `updateRSETHPrice()`, not at deposit time.

### Recommendation
Add staleness and validity checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    // Optionally: if (block.timestamp - updatedAt > HEARTBEAT) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

### Proof of Concept

**Vulnerable code path:** [1](#0-0) 

All five return values are available but only `price` is read — `updatedAt`, `answeredInRound`, and `roundId` are silently discarded. [2](#0-1) 

`LRTOracle.getAssetPrice()` delegates directly to the unchecked `ChainlinkPriceOracle`. [3](#0-2) 

The stale price flows into rsETH mint calculation: `(amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()`. A stale-high asset price inflates `rsethAmountToMint`.

**Contrast — the same protocol correctly validates Chainlink data elsewhere:** [4](#0-3) 

`ChainlinkOracleForRSETHPoolCollateral.getRate()` checks `answeredInRound`, `timestamp`, and `ethPrice` before returning — the exact checks absent from `ChainlinkPriceOracle`.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTDepositPool.sol (L511-521)
```text
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L26-37)
```text
    function getRate() public view returns (uint256) {
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
    }
```
