### Title
Missing Chainlink `latestRoundData` Validation Allows Stale/Invalid Price Consumption - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards all validation return values. No staleness check, round-completeness check, or positive-price check is performed. The same contract codebase already demonstrates the correct pattern in `ChainlinkOracleForRSETHPoolCollateral`, making this an inconsistency with a concrete impact path.

### Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values of `latestRoundData()` — `roundId`, `answer`, `startedAt`, `updatedAt`, `answeredInRound` — are available, but only `answer` is used. The three critical guards are absent:

| Check | Missing in `ChainlinkPriceOracle` | Present in `ChainlinkOracleForRSETHPoolCollateral` |
|---|---|---|
| Staleness (`updatedAt`) | ✗ | ✓ (`if (timestamp == 0)`) |
| Round completeness | ✗ | ✓ (`if (answeredInRound < roundID)`) |
| Positive price | ✗ | ✓ (`if (ethPrice <= 0)`) | [1](#0-0) [2](#0-1) 

`ChainlinkPriceOracle.getAssetPrice()` feeds into two critical protocol paths:

1. **Deposit minting** — `LRTDepositPool.depositAsset()` → `_beforeDeposit()` → `getRsETHAmountToMint()` → `LRTOracle.getAssetPrice()` → `ChainlinkPriceOracle.getAssetPrice()`. The returned price directly determines how many rsETH tokens are minted per deposited LST. [3](#0-2) 

2. **rsETH price update** — `LRTOracle._getTotalEthInProtocol()` iterates all supported assets and calls `getAssetPrice()` for each, summing their ETH value. This total drives `_updateRsETHPrice()`, which sets the global `rsETHPrice` used by all subsequent deposits and withdrawals. [4](#0-3) 

### Impact Explanation

**Stale price scenario (most realistic):** During a Chainlink feed disruption (e.g., L2 sequencer downtime, network congestion, or a feed heartbeat miss), the last reported price remains in the feed. If the stale price is lower than the true market price of the LST:

- `getRsETHAmountToMint()` returns a higher-than-correct rsETH amount for the depositor.
- The depositor receives excess rsETH, diluting the share value of all existing rsETH holders — this is **theft of unclaimed yield** from existing holders.
- `_getTotalEthInProtocol()` also uses the stale price, causing `rsETHPrice` to be set incorrectly, compounding the mispricing across all subsequent operations.

**Zero price scenario:** If `price = 0` (possible during extreme feed failure), `uint256(0) = 0`, so `rsethAmountToMint = 0`. A depositor's assets are transferred in (`safeTransferFrom` succeeds) but 0 rsETH is minted — a **temporary freeze of deposited funds** since the user has no rsETH to redeem. [5](#0-4) 

**Negative price scenario:** `uint256(negative_int256)` wraps to a near-`2^256` value, causing `rsethAmountToMint` to be astronomically large. An attacker depositing a minimal LST amount would receive an unbounded rsETH mint — **direct theft of all user funds / protocol insolvency**. [1](#0-0) 

### Likelihood Explanation

Chainlink feed disruptions (stale rounds, sequencer downtime on L2s, heartbeat misses during low-volatility periods) are documented historical events. The protocol supports multiple LST assets each with their own feed, increasing the attack surface. The zero/negative price scenarios are lower probability but the stale price scenario is a realistic operational risk. No admin action is required — any unprivileged depositor can call `depositAsset()` during a feed disruption.

### Recommendation

Apply the same validation pattern already used in `ChainlinkOracleForRSETHPoolCollateral.getRate()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    if (block.timestamp - updatedAt > STALENESS_THRESHOLD) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`STALENESS_THRESHOLD` should be set per-feed based on the feed's documented heartbeat (e.g., 3600 seconds for a 1-hour heartbeat feed).

### Proof of Concept

1. Chainlink's LST/ETH feed for a supported asset (e.g., stETH/ETH) enters a stale round — `updatedAt` is 2+ hours old but `answeredInRound < roundId` is not yet triggered.
2. The stale price reflects a value 2% below the current true price (a realistic deviation during a rapid LST appreciation event).
3. An attacker calls `LRTDepositPool.depositAsset(stETH, 100e18, 0, "")`.
4. `getRsETHAmountToMint()` calls `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns the stale low price.
5. The attacker receives ~2% more rsETH than the correct amount, at the expense of existing rsETH holders whose share value is diluted.
6. No revert occurs anywhere in the call chain because `ChainlinkPriceOracle` performs no validation. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L27-32)
```text
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();
```

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
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

**File:** contracts/LRTDepositPool.sol (L648-669)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
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
