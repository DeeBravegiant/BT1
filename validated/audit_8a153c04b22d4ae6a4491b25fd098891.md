### Title
Missing Negative Price Validation in `ChainlinkPriceOracle` Enables Inflated rsETH Exchange Rate — (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` casts the `int256 answer` returned by Chainlink's `latestRoundData()` directly to `uint256` without any sign check. If a feed returns a negative value, the cast silently wraps to an astronomically large number, inflating `totalETHInProtocol` and consequently `rsETHPrice`. The same codebase already applies the correct guard in `ChainlinkOracleForRSETHPoolCollateral`, making the omission in `ChainlinkPriceOracle` a clear inconsistency.

---

### Finding Description

In `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol  lines 52-54
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

There is no guard on `price`. If `price == -1`, then `uint256(-1)` evaluates to `2²⁵⁶ − 1 ≈ 1.16 × 10⁷⁷`, a value that completely overwhelms any realistic asset balance.

Contrast this with the correct pattern already present in the same repository:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol  line 32
if (ethPrice <= 0) revert InvalidPrice();
```

The vulnerable oracle is the one wired into the core protocol. `LRTOracle.getAssetPrice()` delegates to `IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset)`, and `assetPriceOracle` is populated with `ChainlinkPriceOracle` instances for supported LSTs. `_getTotalEthInProtocol()` calls `getAssetPrice()` for every supported asset and multiplies the result by the asset's total balance. A single negative feed response inflates the entire protocol TVL.

---

### Impact Explanation

**Vulnerability class:** oracle/rate abuse → share/asset mis-accounting.

The inflated `totalETHInProtocol` flows directly into `_updateRsETHPrice()`:

```solidity
// contracts/LRTOracle.sol  line 250
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

Two scenarios follow depending on the value of `pricePercentageLimit`:

| `pricePercentageLimit` | Outcome |
|---|---|
| `0` (default, no limit set) | `isPriceIncreaseOffLimit = false`; the inflated price is written to `rsETHPrice`. New depositors receive near-zero rsETH for their ETH/LST (permanent loss). Existing holders redeeming at the inflated rate drain the protocol (fund theft). |
| `> 0` | `isPriceIncreaseOffLimit = true`; `updateRSETHPrice()` reverts for every non-manager caller, freezing the price oracle until a manager intervenes (temporary DoS of price updates). |

The default state of `pricePercentageLimit` is `0` (no initializer sets it), making the fund-loss path the default.

---

### Likelihood Explanation

Chainlink LST/ETH feeds do not routinely return negative answers, so this requires an abnormal feed condition (e.g., a feed misconfiguration, a newly added feed with an inverted sign convention, or a sequencer/aggregator edge case). The entry path — `updateRSETHPrice()` — is public and callable by any address with no access restriction, so no privileged actor is needed once the feed condition exists. Likelihood is **Low**, but the impact when triggered is **Critical** (fund theft / permanent loss for depositors).

---

### Recommendation

Add a non-negative guard before the cast, consistent with `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol
(, int256 price,,,) = priceFeed.latestRoundData();
if (price <= 0) revert InvalidPrice();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

---

### Proof of Concept

1. A supported LST's Chainlink feed returns `answer = -1` (any negative value).
2. Any address calls `LRTOracle.updateRSETHPrice()` (no access control).
3. `LRTOracle._getTotalEthInProtocol()` calls `ChainlinkPriceOracle.getAssetPrice(lst)`.
4. `uint256(-1)` = `2²⁵⁶ − 1`; multiplied by the asset balance, `totalETHInProtocol` overflows to an astronomically large value.
5. `newRsETHPrice = totalETHInProtocol / rsethSupply` is set to a value orders of magnitude above the real price.
6. Because `pricePercentageLimit` defaults to `0`, `isPriceIncreaseOffLimit = false`; the check at line 260 is skipped.
7. `rsETHPrice` is written with the inflated value (line 313).
8. Any subsequent depositor calling `LRTDepositPool.depositAsset()` receives `depositAmount / inflatedPrice ≈ 0` rsETH — their funds are effectively confiscated.
9. Any existing rsETH holder who redeems receives `rsETHAmount × inflatedPrice` ETH, draining the protocol.

**Relevant code references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** contracts/LRTOracle.sol (L249-257)
```text
        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```

**File:** contracts/LRTOracle.sol (L312-315)
```text

        rsETHPrice = newRsETHPrice;

        emit RsETHPriceUpdate(rsETHPrice, previousPrice);
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
