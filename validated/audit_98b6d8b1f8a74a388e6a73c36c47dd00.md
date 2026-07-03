### Title
Chainlink Oracle Staleness Not Validated in `getAssetPrice`, Enabling Stale-Price Deposit Exploitation - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards all freshness fields (`updatedAt`, `answeredInRound`, `roundId`). When a Chainlink feed goes stale at an inflated price, any depositor can call `LRTDepositPool.depositAsset()` and receive excess rsETH, directly stealing value from existing rsETH holders.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

The return tuple `(roundId, answer, startedAt, updatedAt, answeredInRound)` is destructured with only `answer` captured. `updatedAt` (the timestamp of the last price update) and `answeredInRound` (used to detect incomplete rounds) are both discarded with no validation. [1](#0-0) 

This is the direct analog to the reported Pyth vulnerability: just as passing an empty `priceUpdateData` array causes the Pyth oracle to silently skip the update and return a cached stale price, here the contract unconditionally accepts whatever `latestRoundData()` returns — including data from a round that has not been updated for hours or days.

The same repository already contains a correct implementation in `ChainlinkOracleForRSETHPoolCollateral`, which validates both fields:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
``` [2](#0-1) 

The stale price returned by `ChainlinkPriceOracle.getAssetPrice()` flows directly into `LRTOracle.getAssetPrice()`, which is consumed by `LRTDepositPool.getRsETHAmountToMint()`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

This is the minting formula used in the public `depositAsset()` entry point. [4](#0-3) 

---

### Impact Explanation

**Critical — Direct theft of user funds at rest.**

If a supported LST's Chainlink feed is stale at an inflated price (e.g., stETH depegs but the oracle has not updated), an attacker deposits that LST and receives rsETH computed against the old high price. The attacker's rsETH represents a larger share of the protocol's TVL than the assets they contributed. When the oracle eventually updates, `updateRSETHPrice()` recalculates the true (lower) TVL, reducing `rsETHPrice` for all holders. The attacker's excess rsETH is redeemable at the expense of every existing rsETH holder — a direct, quantifiable theft of funds at rest.

---

### Likelihood Explanation

**Medium.** Chainlink feeds have documented heartbeat intervals (e.g., 1 hour for ETH/USD on mainnet, up to 24 hours for some LST feeds). During network congestion, oracle node downtime, or L2 sequencer outages, feeds can go stale well beyond their heartbeat. The protocol is deployed across many L2 networks where sequencer downtime is a known risk. No special permissions are required — any depositor can exploit the window.

---

### Recommendation

Apply the same staleness and completeness checks already present in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

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

Additionally, consider adding a configurable `maxStaleness` threshold (e.g., `block.timestamp - updatedAt > maxStaleness`) per asset, since heartbeat intervals differ across feeds.

---

### Proof of Concept

**Actors:**
- Alice: existing rsETH holder (deposited 1 ETH, holds rsETH worth 1 ETH)
- Bob: attacker

**Setup:**
- stETH/ETH Chainlink feed last updated 4 hours ago at `1.001e18` (stETH at a slight premium)
- stETH has since depegged; true market price is `0.95e18`
- `rsETHPrice` = `1.001e18` (last oracle update)

**Exploit:**
1. Bob observes the Chainlink feed is stale (heartbeat missed due to network congestion).
2. Bob calls `LRTDepositPool.depositAsset(stETH, 100e18, 0, "")`.
3. `getRsETHAmountToMint(stETH, 100e18)` calls `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns stale `1.001e18`.
4. `rsethAmountToMint = (100e18 * 1.001e18) / 1.001e18 = 100e18` rsETH minted.
5. Bob's 100 stETH is actually worth only `100 * 0.95 = 95 ETH` at true market price.
6. Bob received rsETH worth 100 ETH in protocol accounting — a 5 ETH overallocation.
7. When `updateRSETHPrice()` is next called, `_getTotalEthInProtocol()` uses the same stale oracle and inflates TVL, but once the feed updates, the true TVL is lower, reducing `rsETHPrice` for all holders including Alice.
8. Alice's rsETH is now worth less than 1 ETH. Bob extracted ~5 ETH of value from existing holders. [1](#0-0) [5](#0-4) [6](#0-5)

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

**File:** contracts/LRTDepositPool.sol (L100-118)
```text
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

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
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
