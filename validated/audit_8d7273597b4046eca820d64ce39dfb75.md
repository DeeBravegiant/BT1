### Title
Stale Chainlink Price Accepted Without Timestamp Validation, Enabling Oracle Rate Abuse at Deposit - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards the `updatedAt` return value entirely, accepting arbitrarily stale prices with no heartbeat or staleness guard. Because this oracle is the direct price source for rsETH minting in `LRTDepositPool`, a stale Chainlink feed allows any depositor to mint rsETH at a price that no longer reflects the current LST/ETH exchange rate.

---

### Finding Description

In `contracts/oracles/ChainlinkPriceOracle.sol`, `getAssetPrice()` destructures `latestRoundData()` keeping only `price`:

```solidity
// ChainlinkPriceOracle.sol line 52
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

The `updatedAt` field (4th return value) is silently dropped. There is no check of the form `require(block.timestamp - updatedAt <= MAX_STALENESS)`. The price returned may be hours or days old.

This oracle is wired into `LRTOracle.getAssetPrice()`:

```solidity
// LRTOracle.sol line 157
return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
``` [2](#0-1) 

`LRTOracle._getTotalEthInProtocol()` calls `getAssetPrice()` for every supported LST to compute the protocol's total ETH value:

```solidity
// LRTOracle.sol line 339
uint256 assetER = getAssetPrice(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
``` [3](#0-2) 

`LRTDepositPool.getRsETHAmountToMint()` uses `lrtOracle.getAssetPrice(asset)` directly to compute how many rsETH tokens a depositor receives:

```solidity
// LRTDepositPool.sol line 520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [4](#0-3) 

This is called from `_beforeDeposit()`, which is invoked by both `depositETH()` and `depositAsset()` — the primary public entry points for any user. [5](#0-4) 

For contrast, `ChainlinkOracleForRSETHPoolCollateral` (used only for pool collateral) does check `answeredInRound < roundID` and `timestamp == 0`, but still lacks a `block.timestamp - timestamp > MAX_STALENESS` guard and is not used in the core deposit path. [6](#0-5) 

---

### Impact Explanation

**Classification:** Oracle/rate abuse → theft of unclaimed yield (High).

If a Chainlink feed for a supported LST (e.g., stETH/ETH, ETHx/ETH) becomes stale and the last recorded price is **higher** than the current market price:

- `getAssetPrice(asset)` returns an inflated value.
- `getRsETHAmountToMint()` computes `(amount * inflatedPrice) / rsETHPrice`, minting **more rsETH than the deposited assets are worth**.
- The excess rsETH represents a claim on protocol TVL that was not backed by real value, diluting all existing rsETH holders.
- Existing holders' proportional share of the underlying ETH is reduced — this is direct theft of yield from rsETH holders.

If the price deviation is large enough (e.g., a major LST depeg that the feed has not yet reflected), the impact escalates toward protocol insolvency.

---

### Likelihood Explanation

**Medium.** Chainlink feeds can become stale due to:
- RPC/node outages causing oracle reporters to miss heartbeat windows.
- Network congestion preventing oracle transactions from landing.
- A depeg or rapid price movement where the feed lags behind real-time prices.

Chainlink's deviation threshold (e.g., 0.5%) means the feed only updates when price moves enough or the heartbeat fires. During the window between updates, the price is stale. An attacker monitoring mempool can time a deposit to coincide with a known stale feed. No special permissions are required — `depositAsset()` and `depositETH()` are fully public.

---

### Recommendation

Add a configurable `MAX_STALENESS` constant and validate `updatedAt` in `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(, int256 price,, uint256 updatedAt,) = priceFeed.latestRoundData();
require(block.timestamp - updatedAt <= MAX_STALENESS, "Stale price");
require(price > 0, "Invalid price");
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

`MAX_STALENESS` should be set per-feed based on the Chainlink heartbeat (e.g., 3600 seconds for a 1-hour heartbeat feed, with a small buffer). Also apply the same fix to `ChainlinkOracleForRSETHPoolCollateral.getRate()`, which checks round completeness but not elapsed time since `timestamp`.

---

### Proof of Concept

1. Chainlink's stETH/ETH feed last updated at `T-2h` with price `1.001e18` (stETH at a slight premium).
2. stETH depegs to `0.98e18` on-chain, but the Chainlink feed has not yet updated (heartbeat not triggered, deviation threshold not crossed).
3. Attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, 0, "")`.
4. `getRsETHAmountToMint()` computes: `(1000e18 * 1.001e18) / rsETHPrice` — using the stale `1.001e18` price instead of the real `0.98e18`.
5. Attacker receives `~2.1%` more rsETH than the deposited stETH is currently worth.
6. All existing rsETH holders are diluted by this amount; the attacker can immediately redeem or hold the excess rsETH, extracting value from the protocol. [1](#0-0) [7](#0-6) [8](#0-7)

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

**File:** contracts/LRTOracle.sol (L331-349)
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

            unchecked {
                ++assetIdx;
            }
        }
    }
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

**File:** contracts/LRTDepositPool.sol (L648-670)
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
