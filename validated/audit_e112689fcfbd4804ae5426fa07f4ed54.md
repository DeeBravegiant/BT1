### Title
Chainlink Price Feed Staleness Not Validated, Enabling Stale-Rate rsETH Over-Minting — (`contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards every return value except `price`. It never checks `updatedAt` for staleness, never validates `answeredInRound >= roundId`, and never asserts `price > 0`. A stale Chainlink answer for any supported LST (e.g., stETH/ETH) flows directly into `LRTOracle._getTotalEthInProtocol()` and `LRTDepositPool.getRsETHAmountToMint()`, causing depositors to receive an incorrect number of rsETH shares.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` reads only the raw `price` field:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L49-L55
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

The three ignored return values — `updatedAt`, `answeredInRound`, and the implicit `roundId` — are the standard Chainlink staleness guards. None are checked. A zero or negative `price` is also not rejected; `uint256(price)` would silently wrap or return zero.

This price is consumed by two critical paths:

**Path 1 — rsETH price update:**
`LRTOracle._getTotalEthInProtocol()` iterates every supported asset and multiplies its balance by `getAssetPrice(asset)`. The resulting `totalETHInProtocol` is divided by `rsethSupply` to produce `newRsETHPrice`, which is stored as `rsETHPrice`.

**Path 2 — rsETH minting:**
`LRTDepositPool.getRsETHAmountToMint()` computes:
```
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

If `getAssetPrice(asset)` returns a stale inflated value while `rsETHPrice` was last set from correct prices, the numerator is artificially high and the depositor receives more rsETH than fair value.

---

### Impact Explanation

**Theft of unclaimed yield / share dilution (High).**

Existing rsETH holders' shares are diluted when a new depositor receives excess rsETH minted against a stale-high asset price. For example:

- Correct stETH/ETH rate: 1.000 ETH; stale Chainlink answer: 1.010 ETH (within the 0.5 % deviation band, so no circuit-breaker fires).
- `rsETHPrice` was last updated at 1.05 ETH/rsETH.
- Depositor sends 100 stETH and receives `100 * 1.010 / 1.05 ≈ 96.19 rsETH` instead of the fair `100 * 1.000 / 1.05 ≈ 95.24 rsETH`.
- The ~0.95 rsETH surplus is extracted from the pool at the expense of all existing holders.

The same mechanism applies to rETH (via `RETHPriceOracle` which calls `getExchangeRate()` directly on the rETH contract — no Chainlink staleness issue there), but stETH/ETH and any other asset backed by a Chainlink feed is fully exposed.

---

### Likelihood Explanation

**Medium.** Chainlink feeds can lag during L1 network congestion, sequencer downtime on L2, or when the price moves within the deviation threshold (0.5 % for stETH/ETH). During such windows — which can last hours — the stale price is silently accepted. No special attacker capability is required: any depositor who monitors Chainlink round timestamps can identify a stale feed and deposit at the favorable rate.

---

### Recommendation

Add standard Chainlink staleness guards in `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound)
    = priceFeed.latestRoundData();

require(price > 0, "Invalid price");
require(updatedAt != 0, "Round not complete");
require(answeredInRound >= roundId, "Stale round");
require(block.timestamp - updatedAt <= MAX_STALENESS, "Price too stale");
```

`MAX_STALENESS` should be set per-feed based on its heartbeat (e.g., 3 600 s for a 1-hour heartbeat feed).

---

### Proof of Concept

1. Chainlink stETH/ETH feed last updated 2 hours ago at 1.010 ETH (within the 0.5 % deviation band; no new round triggered).
2. Attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, "")`.
3. `getRsETHAmountToMint` calls `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns stale 1.010e18.
4. `rsETHPrice` is 1.05e18 (last correctly updated).
5. Attacker receives `1000 * 1.010 / 1.05 ≈ 961.9 rsETH` instead of fair `1000 * 1.000 / 1.05 ≈ 952.4 rsETH`.
6. ~9.5 rsETH of value is extracted from existing holders with no admin action required. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
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

**File:** contracts/LRTDepositPool.sol (L515-521)
```text
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
