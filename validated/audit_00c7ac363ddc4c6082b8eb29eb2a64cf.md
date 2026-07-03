### Title
Stale Chainlink Price Accepted in `ChainlinkPriceOracle.getAssetPrice()` Corrupts rsETH Exchange Rate - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards every return value except the raw `int256 price`, performing no staleness, round-completeness, or sign check. A stale Chainlink answer is silently accepted and propagated into `LRTOracle._updateRsETHPrice()`, which sets the global `rsETHPrice` used to mint rsETH for all depositors.

---

### Finding Description

`contracts/oracles/ChainlinkPriceOracle.sol` line 52 reads:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

The five return values of `latestRoundData()` are `(roundId, answer, startedAt, updatedAt, answeredInRound)`. The code ignores `updatedAt` (staleness), `answeredInRound` (round completeness), and does not verify `price > 0`.

Contrast this with `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol`, which is a sibling contract in the same repository and correctly validates all three conditions:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The vulnerable `getAssetPrice()` feeds directly into `LRTOracle._getTotalEthInProtocol()` (line 339), which is called by `LRTOracle._updateRsETHPrice()` (line 231). That function computes `newRsETHPrice` (line 250) and writes it to the global `rsETHPrice` state variable (line 313). `rsETHPrice` is the exchange rate used by `LRTDepositPool` to determine how many rsETH tokens to mint per deposited LST.

`LRTOracle.updateRSETHPrice()` is a **public, permissionless function** (line 87), meaning any external caller can trigger a price update at any time.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

When a Chainlink feed goes stale (e.g., during network congestion, oracle downtime, or a heartbeat miss), the last reported price remains in the feed. If that stale price is lower than the true current LST/ETH rate, `_getTotalEthInProtocol()` underestimates the protocol's TVL, causing `newRsETHPrice` to be set below its true value. An attacker who calls `updateRSETHPrice()` at this moment locks in the depressed `rsETHPrice`. They then immediately deposit LSTs via `LRTDepositPool.depositAsset()` and receive more rsETH than the assets are worth at the true price. This dilutes all existing rsETH holders, constituting theft of their accrued yield.

Conversely, a stale price that is higher than reality inflates `rsETHPrice`, causing depositors to receive fewer rsETH tokens than they are entitled to — a failure to deliver promised returns.

---

### Likelihood Explanation

Chainlink feeds have documented heartbeat intervals (e.g., 24 hours for some LST/ETH feeds) and deviation thresholds. During periods of low volatility or network stress, feeds can remain at their last reported value for the full heartbeat window. Because `updateRSETHPrice()` is public and callable by anyone, an attacker can monitor on-chain feed timestamps and call the function precisely when a feed is stale. No privileged access is required.

---

### Recommendation

Add staleness and validity checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral.getRate()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    if (block.timestamp - updatedAt > MAX_STALENESS_PERIOD) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`MAX_STALENESS_PERIOD` should be set per-asset based on the Chainlink feed's documented heartbeat interval.

---

### Proof of Concept

1. Assume stETH/ETH Chainlink feed has a 24-hour heartbeat. At `T=0` the feed reports `0.9990e18`. At `T=23h` the true rate is `1.0010e18` but the feed has not updated yet (`updatedAt` is 23 hours old).

2. Attacker calls `LRTOracle.updateRSETHPrice()` (public, no access control).

3. `_updateRsETHPrice()` calls `_getTotalEthInProtocol()` → `getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns stale `0.9990e18` instead of `1.0010e18`.

4. `totalETHInProtocol` is understated; `newRsETHPrice` is set below true value and written to `rsETHPrice`.

5. Attacker immediately calls `LRTDepositPool.depositAsset(stETH, amount, ...)`. The minting calculation uses the now-stale (depressed) `rsETHPrice`, minting excess rsETH relative to the true asset value.

6. All existing rsETH holders are diluted; the attacker captures the difference as profit.

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

**File:** contracts/LRTOracle.sol (L249-251)
```text
        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
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
