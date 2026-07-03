### Title
`ChainlinkPriceOracle.getAssetPrice()` Returns Clamped `minAnswer` During LST Depeg, Enabling rsETH Over-Minting — (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` and uses the returned price without validating it against the Chainlink aggregator's built-in `minAnswer` circuit breaker. If a supported LST asset depegs below `minAnswer`, the oracle silently returns the floor price instead of the true market price, inflating the protocol's TVL and the rsETH exchange rate. An unprivileged depositor can exploit this to mint excess rsETH and drain healthy collateral from the pool.

---

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

No check is made against the aggregator's `minAnswer`. Chainlink aggregators have a hardcoded lower bound; when the true market price falls below it, `latestRoundData()` returns `minAnswer` rather than the actual price. The returned `price` is used directly.

This price flows into `LRTOracle.getAssetPrice()`:

```solidity
// contracts/LRTOracle.sol L156-158
function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
    return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
}
``` [2](#0-1) 

Which is consumed by `_getTotalEthInProtocol()` to compute the protocol's total ETH value:

```solidity
// contracts/LRTOracle.sol L336-343
uint256 assetER = getAssetPrice(asset);
uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
``` [3](#0-2) 

The inflated `totalETHInProtocol` propagates into `_updateRsETHPrice()`, which sets `rsETHPrice` to an inflated value. `LRTDepositPool.getRsETHAmountToMint()` then uses both the inflated asset price and the inflated rsETH price to compute how many rsETH tokens to mint per deposited asset:

```solidity
// contracts/LRTDepositPool.sol L519-521
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [4](#0-3) 

Because both numerator (`getAssetPrice(asset)` = inflated `minAnswer`) and denominator (`rsETHPrice` = inflated from the same source) are inflated by the same oracle, the ratio may appear neutral at first glance. However, the critical window is **between** the depeg event and the next `updateRSETHPrice()` call: `rsETHPrice` is a stored state variable updated only when `updateRSETHPrice()` is called, while `getAssetPrice()` is read live. An attacker who deposits the depegged asset immediately after the depeg (before `updateRSETHPrice()` is called) receives rsETH priced at the stale (pre-depeg) `rsETHPrice` but with the inflated `minAnswer` asset price in the numerator, yielding excess rsETH relative to the asset's true value.

The `ChainlinkPriceOracle` is deployed on mainnet at `0x78C12ccE8346B936117655Dd3D70a2501Fd3d6e6` and is actively used to price stETH, rETH, ETH-X, swETH, and sfrxETH. [1](#0-0) 

---

### Impact Explanation

**Critical — Direct theft of user funds.**

An attacker deposits a depegged LST at the inflated `minAnswer` price (before `rsETHPrice` is updated), receives excess rsETH, then redeems or sells that rsETH for healthy assets (ETH or other LSTs held in the pool). The loss is borne by all other rsETH holders whose share of the pool is diluted.

---

### Likelihood Explanation

**Medium.** LST depeg events are rare but have occurred (stETH in 2022, LUNA-adjacent events). The protocol holds multiple LSTs (stETH, rETH, ETH-X, swETH, sfrxETH), each with its own Chainlink feed and `minAnswer`. The attack window is the block interval between the depeg and the next `updateRSETHPrice()` call, which is publicly callable by anyone — but the attacker can act in the same block as the depeg before any keeper updates the price.

---

### Recommendation

In `ChainlinkPriceOracle.getAssetPrice()`, retrieve the aggregator's `minAnswer` and `maxAnswer` from the `IAccessControlledOffchainAggregator` interface and revert if the returned price is at or outside those bounds:

```solidity
IAccessControlledOffchainAggregator aggregator =
    IAccessControlledOffchainAggregator(priceFeed.aggregator());
int192 minAnswer = aggregator.minAnswer();
int192 maxAnswer = aggregator.maxAnswer();
if (price <= minAnswer || price >= maxAnswer) revert PriceOutOfBounds();
```

This mirrors the mitigation recommended in the original report and is the standard defense against Chainlink circuit-breaker abuse.

---

### Proof of Concept

1. Assume stETH is a supported asset with a Chainlink feed whose `minAnswer` = 0.9e18 (90% of ETH).
2. A black-swan event causes stETH's true market price to drop to 0.5e18.
3. Chainlink's aggregator clamps the return value to `minAnswer` = 0.9e18.
4. `ChainlinkPriceOracle.getAssetPrice(stETH)` returns `0.9e18`.
5. The stored `rsETHPrice` is still `1.0e18` (not yet updated).
6. Attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, ...)`.
7. `getRsETHAmountToMint` computes: `1000e18 * 0.9e18 / 1.0e18 = 900 rsETH`.
8. True value of 1000 stETH = 500 ETH; attacker receives 900 rsETH worth ~900 ETH at the stale price.
9. Attacker redeems 900 rsETH for ~900 ETH worth of healthy assets, netting ~400 ETH profit at the expense of existing depositors. [1](#0-0) [4](#0-3) [3](#0-2)

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

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
