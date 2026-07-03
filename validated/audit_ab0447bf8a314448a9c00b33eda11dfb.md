### Title
Missing Chainlink Oracle Staleness Check Allows Stale Prices to Corrupt rsETH Exchange Rate - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls Chainlink's `latestRoundData()` but discards every return value except `price`. No staleness guard — no `updatedAt` heartbeat check, no `answeredInRound < roundId` check, no zero-price check — is applied. This is the direct Solidity analog of the reported "missing timeout" pattern: just as `downloadS3Data` was invoked without a context deadline, the Chainlink feed is queried without any freshness deadline, allowing an indefinitely stale price to silently propagate into the rsETH exchange rate.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` is the primary price source for all supported LST assets in the protocol:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol:52-54
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values of `latestRoundData()` — `roundId`, `answer`, `startedAt`, `updatedAt`, `answeredInRound` — are available, but only `answer` is used. [1](#0-0) 

This price flows directly into `LRTOracle._getTotalEthInProtocol()` via `getAssetPrice(asset)`, which sums the ETH value of every supported asset, and then into `_updateRsETHPrice()`, which sets the canonical `rsETHPrice` used for all minting and withdrawal calculations. [2](#0-1) 

By contrast, the protocol's own `ChainlinkOracleForRSETHPoolCollateral` — used for pool collateral — does apply partial staleness guards (`answeredInRound < roundID`, `timestamp == 0`, `ethPrice <= 0`), demonstrating that the team is aware of the requirement but failed to apply it to the core oracle. [3](#0-2) 

`ChainlinkPriceOracle` has none of these checks. [4](#0-3) 

---

### Impact Explanation

If a Chainlink feed for any supported LST (e.g., stETH/ETH, rETH/ETH) stops updating — due to network congestion, oracle node failure, or a sequencer outage on L2 — `latestRoundData()` continues returning the last cached answer without reverting. The stale price is silently accepted and used to compute `totalETHInProtocol`.

- **Inflated stale price**: `totalETHInProtocol` is overstated → `newRsETHPrice` rises → new depositors receive fewer rsETH tokens than the true rate warrants, while the fee-minting logic may incorrectly mint protocol fees against phantom yield. Existing holders are diluted.
- **Deflated stale price**: `totalETHInProtocol` is understated → `newRsETHPrice` drops → the downside-protection logic in `_updateRsETHPrice()` may trigger an erroneous protocol-wide pause, temporarily freezing all deposits and withdrawals. [5](#0-4) 

The most realistic impact is **theft of unclaimed yield** (High): a stale inflated price causes the oracle to record phantom TVL growth, triggering unearned protocol fee minting to the treasury at the expense of rsETH holders. [6](#0-5) 

---

### Likelihood Explanation

Chainlink feed staleness is a well-documented, recurring on-chain event. Feeds have heartbeat intervals (e.g., 1 hour for ETH/USD, 24 hours for some LST feeds). During periods of low volatility, feeds may not update for the full heartbeat window. Any depositor interacting with `LRTDepositPool` during a stale window triggers the vulnerable path with no special preconditions.

---

### Recommendation

Add a configurable `maxStaleness` parameter per asset feed and validate `updatedAt` in `getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

if (answeredInRound < roundId) revert StalePrice();
if (updatedAt == 0) revert IncompleteRound();
if (price <= 0) revert InvalidPrice();
if (block.timestamp - updatedAt > maxStaleness[asset]) revert PriceOutdated();
```

This mirrors the pattern already present in `ChainlinkOracleForRSETHPoolCollateral` (minus the heartbeat check, which should also be added there).

---

### Proof of Concept

1. Chainlink's stETH/ETH feed stops updating (oracle node outage or low-volatility heartbeat window).
2. `latestRoundData()` returns the last cached `price` with a stale `updatedAt`.
3. Any user calls `LRTDepositPool.depositAsset(stETH, amount)`.
4. Internally, `LRTOracle.updateRSETHPrice()` → `_getTotalEthInProtocol()` → `ChainlinkPriceOracle.getAssetPrice(stETH)` returns the stale price with no revert.
5. `_updateRsETHPrice()` computes `newRsETHPrice` using the stale TVL, potentially minting unearned protocol fees to the treasury or issuing rsETH at an incorrect rate to the depositor.
6. No admin action is required; the path is fully reachable by any unprivileged depositor. [4](#0-3) [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTOracle.sol (L214-232)
```text
    function _updateRsETHPrice() internal {
        address rsETHTokenAddress = lrtConfig.rsETH();
        uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();

        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }

        if (highestRsethPrice == 0) {
            highestRsethPrice = rsETHPrice;
        }

        uint256 previousPrice = rsETHPrice;

        // get total ETH in the protocol (normalized to 1e18)
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

```

**File:** contracts/LRTOracle.sol (L270-281)
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
```

**File:** contracts/LRTOracle.sol (L299-307)
```text
        if (protocolFeeInETH > 0) {
            // Calculate rsETH amount to mint as protocol fee
            uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);

            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
            if (rsethAmountToMintAsProtocolFee > 0) {
                address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
                IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
                emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
```

**File:** contracts/LRTOracle.sol (L331-343)
```text
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L27-33)
```text
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

```
