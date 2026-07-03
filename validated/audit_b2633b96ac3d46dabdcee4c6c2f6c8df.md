### Title
Missing Chainlink Oracle Staleness Validation Causes Inaccurate rsETH Price Calculation - (`contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle::getAssetPrice` reads from Chainlink's `latestRoundData()` without performing any of the safety checks that Chainlink's own documentation requires. This is the direct analog to M-03: a data-query function is called without the documented validity checks, and the result is used for financial accounting. The stale or invalid price propagates into `LRTOracle::_getTotalEthInProtocol`, which sets the on-chain `rsETHPrice` used to mint rsETH for every depositor.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice` calls `latestRoundData()` and discards every field except `price`:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol  line 52
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

Chainlink's documented requirements for safe consumption of `latestRoundData()` are:
- `answeredInRound >= roundId` — detects a stale round
- `updatedAt > 0` — detects an incomplete round
- `block.timestamp - updatedAt <= maxStaleness` — detects a time-expired answer

All three fields (`roundId`, `updatedAt`, `answeredInRound`) are silently discarded with `,,,,`. No revert path exists for any staleness condition.

Contrast this with `ChainlinkOracleForRSETHPoolCollateral`, which at least attempts partial validation:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol  lines 30-32
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The core `ChainlinkPriceOracle` used for L1 asset pricing has none of these guards.

The call chain is:

```
LRTOracle::_getTotalEthInProtocol()
  → LRTOracle::getAssetPrice(asset)          [line 157]
    → ChainlinkPriceOracle::getAssetPrice()  [line 52]
      → latestRoundData() — no staleness check
  → totalETHInProtocol += totalAssetAmt.mulWad(assetER)  [line 343]

LRTOracle::_updateRsETHPrice()
  → rsETHPrice = newRsETHPrice               [line 313]

LRTDepositPool::depositAsset / depositETH
  → _beforeDeposit → getRsETHAmountToMint
    → lrtOracle.rsETHPrice()                 [used for mint calculation]
```

---

### Impact Explanation

If a Chainlink feed for any supported LST (stETH, rETH, etc.) becomes stale — during network congestion, oracle downtime, or an L2 sequencer outage — `_getTotalEthInProtocol` returns an incorrect TVL. `_updateRsETHPrice` then writes an incorrect `rsETHPrice` to storage. Every subsequent deposit mints rsETH at the wrong rate:

- **Stale price too low** (oracle lags behind a real price increase): TVL is understated → `rsETHPrice` is set below fair value → new depositors receive more rsETH than they are entitled to → existing rsETH holders are diluted (theft of yield).
- **Stale price too high** (oracle lags behind a real price drop): TVL is overstated → `rsETHPrice` is set above fair value → new depositors receive fewer rsETH than they are entitled to → contract fails to deliver promised returns.

The first scenario is directly exploitable: an attacker monitors for a stale feed, calls `updateRSETHPrice()` while the feed is stale, then immediately deposits to capture the mispriced rsETH.

Impact: **High — Theft of unclaimed yield / share mis-accounting**.

---

### Likelihood Explanation

Chainlink feeds on Ethereum L1 have heartbeat intervals (e.g., 24 hours for stETH/ETH). During periods of low volatility the feed may not update for the full heartbeat window. Any caller can invoke `updateRSETHPrice()` at any time (it is a public function with no access control). An attacker can time the call to coincide with a stale feed window. This requires no privileged access and is reachable by any external caller.

---

### Recommendation

Add staleness validation in `ChainlinkPriceOracle::getAssetPrice`, mirroring the pattern already present in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (block.timestamp - updatedAt > MAX_STALENESS) revert PriceExpired();
    if (price <= 0) revert InvalidPrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`MAX_STALENESS` should be set per-feed based on the Chainlink heartbeat (e.g., 25 hours for a 24-hour heartbeat feed).

---

### Proof of Concept

1. Chainlink stETH/ETH feed on Ethereum L1 has a 24-hour heartbeat. Assume it last updated 23 hours ago at price `1.05e18`.
2. The real stETH price drops to `1.00e18` due to a slashing event, but the oracle has not yet updated.
3. Attacker calls `LRTOracle::updateRSETHPrice()` (public, no access control).
4. `_getTotalEthInProtocol()` calls `ChainlinkPriceOracle::getAssetPrice(stETH)` → returns stale `1.05e18`.
5. TVL is overstated by 5% → `rsETHPrice` is set 5% above fair value.
6. Attacker deposits ETH into `LRTDepositPool::depositETH()` and receives 5% fewer rsETH than fair value — **or** — if the stale price is below reality, receives 5% more rsETH than fair value, diluting all existing holders.

The root cause — `(, int256 price,,,) = priceFeed.latestRoundData()` with no validation — is the necessary vulnerable step. No admin action is required; the public `updateRSETHPrice()` entry point is sufficient. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
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
