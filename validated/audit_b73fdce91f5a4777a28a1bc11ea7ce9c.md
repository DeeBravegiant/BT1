### Title
No Staleness Check on Chainlink Price Feed Allows rsETH Minting at Stale Asset Prices - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards the `updatedAt` and `answeredInRound` return values, meaning a stale Chainlink price is accepted as valid. This stale price propagates directly into rsETH minting calculations in `LRTDepositPool`, mirroring the M-3 pattern where an oracle can return an outdated value that drives mint/burn decisions.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink round data but only extracts the raw `price`:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L52
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

The `updatedAt` timestamp and `answeredInRound` are never read. No maximum staleness window (e.g., `block.timestamp - updatedAt > MAX_DELAY`) is enforced. If a Chainlink feed stops updating — due to network congestion, a deprecated feed, or any other reason — the function silently returns the last known price regardless of how old it is.

This price is consumed by two critical paths:

**Path 1 — rsETH minting in `LRTDepositPool`:**
`depositAsset()` / `depositETH()` → `_beforeDeposit()` → `getRsETHAmountToMint()` → `lrtOracle.getAssetPrice(asset)` → `ChainlinkPriceOracle.getAssetPrice()`.

The rsETH amount minted is:
```solidity
// contracts/LRTDepositPool.sol L520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**Path 2 — rsETH price update in `LRTOracle`:**
`updateRSETHPrice()` → `_updateRsETHPrice()` → `_getTotalEthInProtocol()` → `getAssetPrice(asset)` → `ChainlinkPriceOracle.getAssetPrice()`.

The stored `rsETHPrice` is recalculated using the stale asset price, which then affects all subsequent minting until the next update.

By contrast, the pool-level oracle `ChainlinkOracleForRSETHPoolCollateral` does perform a partial check (`answeredInRound < roundID`), but `ChainlinkPriceOracle` — used for the core L1 LST assets (stETH, ethX, sfrxETH, rETH) — performs no check at all.

---

### Impact Explanation

**Stale price higher than current market price:** A depositor calling `depositAsset()` receives more rsETH than the deposited asset is worth. This dilutes the share of all existing rsETH holders, constituting theft of their accrued yield.

**Stale price lower than current market price:** Depositors receive fewer rsETH tokens than they are entitled to; the contract fails to deliver promised returns.

In either direction the stale price corrupts the `rsETHPrice` stored in `LRTOracle`, which is also consumed by `RSETHRateProvider.getLatestRate()` and `RSETHPriceFeed.latestRoundData()`, propagating the error to cross-chain rate receivers and any external lending market that integrates the price feed.

Impact classification: **High — theft of unclaimed yield** (stale-high scenario) / **Low — contract fails to deliver promised returns** (stale-low scenario).

---

### Likelihood Explanation

Chainlink feeds have documented heartbeat intervals (e.g., 1 hour for ETH/USD, 24 hours for some LST feeds). During periods of network congestion, a feed can miss its heartbeat. Feeds are also occasionally deprecated and replaced, leaving the old address returning a frozen price indefinitely. Any depositor transacting during such a window triggers the vulnerability without any special capability.

---

### Recommendation

Add a staleness guard in `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
uint256 public constant MAX_STALENESS = 1 hours; // tune per feed

function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (block.timestamp - updatedAt > MAX_STALENESS) revert StalePrice();
    if (price <= 0) revert InvalidPrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Per-asset staleness thresholds should be configurable, since different LST feeds have different heartbeat intervals.

---

### Proof of Concept

1. Assume `stETH/ETH` Chainlink feed last updated at `T-2h` (heartbeat missed).
2. At `T`, the real stETH/ETH rate has dropped from `1.05e18` to `1.00e18` (e.g., slashing event).
3. Attacker calls `LRTDepositPool.depositAsset(stETH, 100e18, 0, "")`.
4. `getRsETHAmountToMint` calls `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns stale `1.05e18`.
5. `rsethAmountToMint = (100e18 * 1.05e18) / rsETHPrice` — attacker receives ~5% more rsETH than the deposited stETH is currently worth.
6. Existing rsETH holders are diluted by the excess minted supply; the attacker can immediately redeem or bridge the surplus rsETH.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
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
