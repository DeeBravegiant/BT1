### Title
Unvalidated Chainlink Price Response in `ChainlinkPriceOracle.getAssetPrice()` Corrupts rsETH Exchange Rate - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but performs no validation on the returned `price` value. A zero, negative, or stale price for any supported LST asset propagates unchecked into `LRTOracle._getTotalEthInProtocol()`, corrupting the computed `rsETHPrice`. This either triggers an unwarranted protocol-wide pause (temporary fund freeze) or allows depositors to mint excess rsETH at the expense of existing holders (protocol insolvency).

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price and immediately casts and returns it without any sanity checks:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L49-55
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Missing checks:
- `price <= 0` — a zero or negative answer is silently accepted
- `answeredInRound < roundId` — stale round detection
- `timestamp == 0` — incomplete round detection

The protocol's own `ChainlinkOracleForRSETHPoolCollateral.getRate()` (used for pool collateral) correctly performs all three checks:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L27-33
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The bad price flows into `LRTOracle._getTotalEthInProtocol()`:

```solidity
// contracts/LRTOracle.sol L336-343
for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
    address asset = supportedAssets[assetIdx];
    uint256 assetER = getAssetPrice(asset);   // ← bad price accepted here
    uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
    totalETHInProtocol += totalAssetAmt.mulWad(assetER);
    ...
}
```

`_getTotalEthInProtocol()` is called by `_updateRsETHPrice()`, which sets the global `rsETHPrice` state variable. `rsETHPrice` is then used in `LRTDepositPool.getRsETHAmountToMint()`:

```solidity
// contracts/LRTDepositPool.sol L520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

---

### Impact Explanation

**Scenario A — price = 0 (zero answer from Chainlink during outage/circuit breaker):**

- `totalETHInProtocol` is underestimated by the full TVL of that asset.
- `newRsETHPrice` drops artificially.
- If the drop exceeds `pricePercentageLimit`, `_updateRsETHPrice()` auto-pauses `LRTDepositPool` and `LRTWithdrawalManager` → **temporary freeze of all user funds**.
- If the drop is within the limit (or `pricePercentageLimit == 0`), `rsETHPrice` is written as too low → depositors calling `depositAsset()` or `depositETH()` receive more rsETH than the assets they contribute are worth → **protocol insolvency** (existing holders are diluted).

**Scenario B — stale price (Chainlink feed not updated):**

- A stale price that diverges from the true market rate causes `rsETHPrice` to be set incorrectly in either direction, enabling arbitrage that extracts value from the protocol.

The impact is **Critical** (protocol insolvency / permanent dilution of existing rsETH holders) in Scenario A when `pricePercentageLimit` is 0 or the price drop is within the configured limit, and **Medium** (temporary fund freeze) when the auto-pause triggers.

---

### Likelihood Explanation

Chainlink feeds are known to return `0` or stale data during network congestion, sequencer downtime (on L2), or when a feed is deprecated. The `updateRSETHPrice()` function is **public and permissionless** — any external caller can trigger it at any time, including immediately after a Chainlink feed returns a bad value. The protocol supports multiple LST assets (stETH, ethX, sfrxETH, rETH), each with its own Chainlink feed, multiplying the surface area. The protocol itself demonstrates awareness of this class of issue by implementing full validation in `ChainlinkOracleForRSETHPoolCollateral`, making the omission in `ChainlinkPriceOracle` a clear inconsistency.

---

### Recommendation

Add the same validation to `ChainlinkPriceOracle.getAssetPrice()` that already exists in `ChainlinkOracleForRSETHPoolCollateral.getRate()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Additionally, consider adding a `block.timestamp - updatedAt > MAX_STALENESS` heartbeat check appropriate to each feed's update frequency.

---

### Proof of Concept

1. Chainlink's stETH/ETH feed returns `price = 0` (e.g., during a circuit breaker event or feed deprecation).
2. Any external caller invokes `LRTOracle.updateRSETHPrice()` (public, no access control).
3. `_updateRsETHPrice()` calls `_getTotalEthInProtocol()`.
4. `getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice()` → returns `0 * 1e18 / decimals = 0`.
5. `totalETHInProtocol` excludes the entire stETH TVL (e.g., 10,000 ETH worth of stETH is counted as 0).
6. `newRsETHPrice = (totalETHInProtocol - 0) / rsethSupply` is significantly lower than the true price.
7. **Path A:** If `pricePercentageLimit > 0` and the drop exceeds it, `LRTDepositPool` and `LRTWithdrawalManager` are paused — all user deposits and withdrawals are frozen until admin intervention.
8. **Path B:** If `pricePercentageLimit == 0`, `rsETHPrice` is written as the deflated value. A depositor immediately calls `depositAsset(stETH, 1 ether, 0, "")`. `getRsETHAmountToMint` computes `(1e18 * getAssetPrice(stETH)) / rsETHPrice` using the artificially low `rsETHPrice`, minting far more rsETH than 1 stETH is worth, diluting all existing rsETH holders.

**Key code references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
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

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
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

**File:** contracts/LRTDepositPool.sol (L99-118)
```text
    function depositAsset(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedERC20Token(asset)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L515-521)
```text
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
