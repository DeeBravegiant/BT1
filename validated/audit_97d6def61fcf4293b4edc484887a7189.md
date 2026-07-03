### Title
Unvalidated Chainlink `latestRoundData()` Response Enables Stale/Invalid Price Consumption, Corrupting rsETH Exchange Rate - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls Chainlink's `latestRoundData()` but discards all validity fields (`updatedAt`, `answeredInRound`, `answer` sign). A stale or invalid price silently propagates into `LRTOracle._updateRsETHPrice()`, corrupting the on-chain `rsETHPrice` used for every deposit, withdrawal, and fee-mint calculation across the protocol.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` reads only the raw `price` field:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

No checks are performed on:
- `updatedAt` — whether the round is recent (staleness)
- `answeredInRound >= roundId` — whether the round is complete
- `price > 0` — whether the answer is valid (negative `int256` cast to `uint256` wraps to `type(uint256).max`)

This is in direct contrast with `ChainlinkOracleForRSETHPoolCollateral`, a sibling contract in the same repository, which explicitly validates all three conditions before returning a price:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The `ChainlinkPriceOracle` is the price source for all supported LST collateral assets (stETH, ETHx, rETH, sfrxETH) on L1. Its output feeds directly into `LRTOracle._getTotalEthInProtocol()`, which computes `totalETHInProtocol`, which in turn determines `rsETHPrice` — the single exchange rate governing all minting and redemption.

`updateRSETHPrice()` is a public, permissionless function. Any caller can trigger a price update at any time, including during a period when a Chainlink feed is stale.

---

### Impact Explanation

**Multi-asset staleness — theft of yield from existing rsETH holders (High)**

When the protocol holds multiple collateral assets and one Chainlink feed goes stale at a price lower than the true market price:

- `totalETHInProtocol` is understated (the stale asset's ETH value is underreported)
- `rsETHPrice = totalETHInProtocol / rsethSupply` is set below its true value
- A depositor of a *non-stale* asset (e.g., ETH) calls `depositETH` and receives `amount * 1e18 / rsETHPrice` rsETH — more rsETH than the deposited ETH is actually worth at the true exchange rate
- This dilutes all existing rsETH holders, extracting their accrued yield

**Negative price wrap-around — temporary freeze of deposits (Medium)**

If a Chainlink feed returns a negative `int256` answer (possible during oracle malfunction), `uint256(price)` wraps to near `type(uint256).max`. This inflates `totalETHInProtocol` to an astronomical value, sets `rsETHPrice` to an astronomical value, and causes `getRsETHAmountToMint` to return 0 for any normal deposit amount, reverting with `MinimumAmountToReceiveNotMet` and effectively freezing deposits until the oracle recovers.

---

### Likelihood Explanation

Chainlink feeds can go stale during:
- Ethereum network congestion (oracle transactions fail to land)
- Chainlink node outages
- Heartbeat gaps on low-volatility feeds (e.g., stETH/ETH has a 24-hour heartbeat)

The attack requires no privileged access. Any external caller can invoke `updateRSETHPrice()` at the moment a feed is stale, locking in the corrupted price. The window can last hours on feeds with long heartbeats.

---

### Recommendation

Add staleness and validity checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    // Optionally: if (block.timestamp - updatedAt > MAX_STALENESS) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

---

### Proof of Concept

1. Assume the protocol holds stETH and ETH. The stETH/ETH Chainlink feed goes stale at `0.990e18` while the true rate is `0.999e18`.
2. Attacker calls `LRTOracle.updateRSETHPrice()` (public, no access control).
3. `_getTotalEthInProtocol()` calls `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns stale `0.990e18`.
4. `totalETHInProtocol` is understated by `stETH_balance * 0.009e18`.
5. `rsETHPrice` is set below its true value.
6. Attacker deposits ETH via `LRTDepositPool.depositETH(minRSETH, "")`, receiving `amount * 1e18 / rsETHPrice` rsETH — more than the true rate entitles them to.
7. When the oracle recovers and `rsETHPrice` is corrected upward, the attacker's rsETH is worth more than deposited, at the expense of all prior rsETH holders.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
