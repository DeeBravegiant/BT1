### Title
Missing Chainlink Price Feed Staleness Check Allows Stale Prices to Corrupt rsETH Exchange Rate - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards the `updatedAt` return value, accepting arbitrarily stale prices with no time-based freshness guard. The stale price propagates through `LRTOracle._updateRsETHPrice()` into the on-chain `rsETHPrice` that every L2 pool deposit calculation depends on, enabling incorrect rsETH minting during any Chainlink feed staleness window.

---

### Finding Description

In `ChainlinkPriceOracle.getAssetPrice()`, the `updatedAt` field returned by `latestRoundData()` is completely discarded:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol line 52
(, int256 price,,,) = priceFeed.latestRoundData();
``` [1](#0-0) 

There is no check of the form `require(block.timestamp - updatedAt <= heartbeat)`. Chainlink feeds update only when price moves beyond a deviation threshold **or** a heartbeat elapses (typically 1h–24h depending on the feed). During network congestion or Chainlink node issues, a feed can remain at a stale price for the full heartbeat window without triggering any revert.

This stale price is consumed by `LRTOracle.getAssetPrice()`, which delegates directly to `ChainlinkPriceOracle`: [2](#0-1) 

`getAssetPrice()` is called inside `_getTotalEthInProtocol()`, which sums the ETH value of all LST assets held by the protocol. That total feeds into `_updateRsETHPrice()`, which computes and stores the new `rsETHPrice`: [3](#0-2) 

`rsETHPrice` is then read by `RSETHRateProvider.getLatestRate()` and broadcast cross-chain: [4](#0-3) 

Every L2 pool variant (`RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, etc.) calls `getRate()` → `IOracle(rsETHOracle).getRate()` to compute how many rsETH tokens to mint per deposited ETH: [5](#0-4) 

By contrast, `ChainlinkOracleForRSETHPoolCollateral.sol` — used for collateral token pricing in the same pools — does attempt a staleness guard, but only checks the round-based `answeredInRound < roundID` condition and not a time-based `block.timestamp - timestamp` bound: [6](#0-5) 

This means a feed that is technically "answered" in the current round but hours old passes the check silently.

---

### Impact Explanation

If the Chainlink feed for a supported LST (e.g., stETH/ETH) goes stale at a price **below** the actual market price:

1. `_getTotalEthInProtocol()` underestimates total ETH held by the protocol.
2. `rsETHPrice` is set below its true value.
3. Any user calling `deposit()` on an L2 pool receives `amountAfterFee * 1e18 / rsETHToETHrate` rsETH — more than their ETH entitles them to.
4. Existing rsETH holders are diluted: the same pool of underlying ETH is now represented by more rsETH tokens, reducing each holder's redemption value.

This constitutes **temporary theft of value from existing rsETH holders** for the duration of the staleness window. The window can last up to the full Chainlink heartbeat (up to 24h for some feeds).

**Impact: Medium — Temporary freezing / mispricing of user funds.**

---

### Likelihood Explanation

Chainlink feeds go stale in practice during:
- Ethereum network congestion (gas spikes preventing keeper updates)
- Chainlink node operational incidents
- Low-volatility periods where the deviation threshold is never crossed within a heartbeat

`updateRSETHPrice()` is a **public, permissionless function** — any user or bot can trigger a price update at any time, including during a staleness window. The stale price is therefore exploitable by any depositor who observes the feed lag and calls `updateRSETHPrice()` followed by `deposit()`.

Likelihood: **Low-Medium** — Chainlink is generally reliable, but the protocol has zero on-chain defense against feed staleness.

---

### Recommendation

Add a configurable per-feed staleness threshold in `ChainlinkPriceOracle`:

```solidity
mapping(address asset => uint256 maxStaleness) public maxStalenessFor;

function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,, uint256 updatedAt,) = priceFeed.latestRoundData();
    require(block.timestamp - updatedAt <= maxStalenessFor[asset], "Stale price");
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Set `maxStalenessFor` per asset to slightly above the Chainlink feed's published heartbeat (e.g., heartbeat + 10 minutes). Apply the same time-based check to `ChainlinkOracleForRSETHPoolCollateral.getRate()` in addition to the existing round-based check.

---

### Proof of Concept

1. Chainlink stETH/ETH feed last updated at price `0.990e18` (actual market: `1.010e18`); feed is within its heartbeat window so no revert occurs.
2. Attacker observes the staleness and calls `LRTOracle.updateRSETHPrice()` (public, no access control).
3. `ChainlinkPriceOracle.getAssetPrice(stETH)` returns `0.990e18`.
4. `_getTotalEthInProtocol()` undervalues stETH holdings by ~2%.
5. `rsETHPrice` is written ~2% below its true value.
6. Attacker immediately calls `RSETHPoolV3.deposit{value: 1 ether}()`.
7. Pool computes `rsETHAmount = 1e18 * 1e18 / rsETHPrice` — attacker receives ~2% more rsETH than entitled.
8. Existing rsETH holders' redemption value is diluted by the excess minted supply.

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

**File:** contracts/LRTOracle.sol (L249-251)
```text
        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

```

**File:** contracts/cross-chain/RSETHRateProvider.sol (L27-29)
```text
    function getLatestRate() public view override returns (uint256) {
        return ILRTOracle(rsETHPriceOracle).rsETHPrice();
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L303-308)
```text
        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
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
