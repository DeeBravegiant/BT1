### Title
Chainlink `minAnswer`/`maxAnswer` circuit breaker not validated in `ChainlinkPriceOracle.getAssetPrice()`, enabling rsETH over-minting during LST price crash - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` and returns the raw price without checking whether it is clamped at the aggregator's `minAnswer` or `maxAnswer` circuit-breaker bounds. If a supported LST asset crashes in value, Chainlink's built-in circuit breaker returns `minAnswer` (a floor price higher than the real price) instead of the actual market price. The protocol then mints rsETH against the crashed asset at the inflated floor price, diluting all existing rsETH holders and creating protocol insolvency.

### Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches the price from a Chainlink aggregator:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L49-55
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
``` [1](#0-0) 

The function discards all return values except `price`. It does not:
- Check `price` against the aggregator's `minAnswer` / `maxAnswer` bounds
- Check `answeredInRound >= roundId` (staleness)
- Check `updatedAt != 0`

Chainlink aggregators have a built-in circuit breaker: when the real market price falls below `minAnswer`, the aggregator continues to report `minAnswer`. This is the exact mechanism that caused the LUNA incident.

This price is consumed by `LRTOracle.getAssetPrice()`: [2](#0-1) 

Which feeds `_getTotalEthInProtocol()` used to compute `rsETHPrice`: [3](#0-2) 

And directly drives `LRTDepositPool.getRsETHAmountToMint()`: [4](#0-3) 

Which is called inside the public `depositAsset()` entry point: [5](#0-4) 

The same unchecked pattern also exists in `ChainlinkOracleForRSETHPoolCollateral.getRate()`, which is used as the collateral token oracle in the RSETHPool family of contracts: [6](#0-5) 

That contract checks `ethPrice <= 0` but does not check against `minAnswer`/`maxAnswer`.

### Impact Explanation
**Critical — Protocol insolvency / direct theft of existing rsETH holder value.**

Scenario: A supported LST (e.g., stETH, rETH, cbETH) crashes from 1 ETH to 0.05 ETH. Chainlink's circuit breaker clamps the reported price at `minAnswer = 0.5 ETH`.

- `getAssetPrice(crashedLST)` returns `0.5 ETH` (10× the real value).
- `_getTotalEthInProtocol()` overestimates the protocol's ETH backing by counting the crashed LST at `0.5 ETH` each.
- `rsETHPrice` is inflated accordingly.
- An attacker calls `depositAsset(crashedLST, amount, ...)`. The minted rsETH is computed as `amount × 0.5 ETH / rsETHPrice`. Because `rsETHPrice` is also inflated by the same crashed asset, the attacker receives rsETH that represents real ETH value far exceeding the actual worth of the deposited crashed LST.
- When the Chainlink price eventually corrects (or the asset is delisted), `rsETHPrice` drops, and all existing rsETH holders suffer a loss proportional to the over-minted supply.

This constitutes direct theft of existing rsETH holder funds and can render the protocol insolvent.

### Likelihood Explanation
**Medium.** Requires a significant, sudden LST price crash (analogous to LUNA/UST). While not a daily occurrence, the LST ecosystem has demonstrated such events are realistic. The attack requires no special permissions — any unprivileged user can call `depositAsset()` during the window when the circuit breaker is active.

### Recommendation
After calling `latestRoundData()`, retrieve the aggregator's `minAnswer` and `maxAnswer` from the underlying `AggregatorInterface` and revert if the returned price is at or outside those bounds:

```solidity
// Pseudocode
IChainlinkAggregator aggregator = IChainlinkAggregator(priceFeed.aggregator());
int192 minAnswer = aggregator.minAnswer();
int192 maxAnswer = aggregator.maxAnswer();
if (price <= minAnswer || price >= maxAnswer) revert PriceOutOfBounds();
```

Apply the same fix to `ChainlinkOracleForRSETHPoolCollateral.getRate()`. Also add staleness checks (`answeredInRound >= roundId` and `updatedAt != 0`) to both contracts.

### Proof of Concept
1. Assume `stETH` is a supported asset with a Chainlink feed whose `minAnswer = 0.5e18` (0.5 ETH).
2. A black-swan event causes stETH to trade at `0.05 ETH` on the open market.
3. Chainlink's circuit breaker activates; `latestRoundData()` returns `price = 0.5e18`.
4. `ChainlinkPriceOracle.getAssetPrice(stETH)` returns `0.5e18` — 10× the real price. [7](#0-6) 
5. `LRTOracle._getTotalEthInProtocol()` sums all assets using this inflated price, producing an inflated `totalETHInProtocol`. [8](#0-7) 
6. `rsETHPrice` is updated to the inflated value.
7. Attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, 0, "")` depositing 1000 stETH worth `50 ETH` in reality. [9](#0-8) 
8. `getRsETHAmountToMint` computes `rsethAmountToMint = (1000e18 × 0.5e18) / rsETHPrice`, minting rsETH backed by `500 ETH` of claimed value against only `50 ETH` of real value. [4](#0-3) 
9. When the price corrects, `rsETHPrice` drops and all existing rsETH holders are diluted by the `450 ETH` phantom value that was minted.

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

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
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
