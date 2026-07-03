### Title
Chainlink `minAnswer`/`maxAnswer` Circuit Breaker Not Checked — Inflated LST Price Allows Over-Minting of rsETH - (`contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` on a Chainlink aggregator but never validates the returned `price` against the aggregator's built-in `minAnswer`/`maxAnswer` circuit breakers. If a supported LST asset crashes below the aggregator's `minAnswer`, the oracle silently returns the floor price instead of the real price, causing rsETH to be minted at an inflated rate.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the price with a bare `latestRoundData()` call:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

There is no check that `price` is above the aggregator's `minAnswer`. Chainlink aggregators clamp their output to `[minAnswer, maxAnswer]`; if the real market price falls below `minAnswer`, the aggregator still reports `minAnswer`.

This price is consumed by `LRTOracle.getAssetPrice()`: [2](#0-1) 

Which is then used directly in `LRTDepositPool.getRsETHAmountToMint()` to determine how many rsETH tokens to mint per unit of deposited asset:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

The same unchecked `latestRoundData()` pattern also appears in `ChainlinkOracleForRSETHPoolCollateral.getRate()`, which is used by pool contracts (`RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`) to price collateral tokens for rsETH swaps: [4](#0-3) 

---

### Impact Explanation

If a supported LST (e.g., stETH, cbETH) crashes and its Chainlink aggregator hits `minAnswer`, an attacker can:

1. Acquire the crashed LST cheaply on the open market at its true low price.
2. Deposit it into `LRTDepositPool` — the oracle reports `minAnswer` (inflated), so `getRsETHAmountToMint` mints far more rsETH than the deposited value warrants.
3. Redeem the over-minted rsETH for ETH, extracting value from all existing rsETH holders.

This is protocol insolvency / direct theft from existing depositors — **Critical** impact.

---

### Likelihood Explanation

Likelihood is **Low**. It requires a supported LST to experience a severe price crash that triggers the Chainlink circuit breaker (analogous to the LUNA collapse). This is a rare but historically documented event (Venus/LUNA on BSC). The `LRTOracle` does have a downside-protection pause mechanism, but it only triggers when `updateRSETHPrice()` is called after the fact; the deposit path reads `getAssetPrice()` live and is not protected. [5](#0-4) 

---

### Recommendation

In `ChainlinkPriceOracle.getAssetPrice()`, store per-feed `minAnswer`/`maxAnswer` bounds and revert if the returned price is outside them:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
if (price <= minAnswer[asset] || price >= maxAnswer[asset]) revert InvalidPrice();
```

Apply the same guard in `ChainlinkOracleForRSETHPoolCollateral.getRate()`.

---

### Proof of Concept

1. Suppose `stETH` is a supported asset with a Chainlink feed whose `minAnswer` = 0.5 ETH and the real market price crashes to 0.05 ETH.
2. Attacker buys 1000 stETH at 0.05 ETH each (cost: 50 ETH).
3. Attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, ...)`.
4. `getRsETHAmountToMint` calls `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns `0.5e18` (minAnswer, not 0.05e18).
5. rsETH minted = `(1000e18 * 0.5e18) / rsETHPrice` — 10× more rsETH than the deposited value warrants.
6. Attacker redeems rsETH for ETH, extracting ~450 ETH of value from existing depositors. [6](#0-5) [7](#0-6)

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

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L27-34)
```text
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
```
