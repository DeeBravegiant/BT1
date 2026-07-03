Audit Report

## Title
Stale Chainlink Price Accepted Without Freshness Validation Enables Over-Minting of rsETH - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards `roundId`, `updatedAt`, and `answeredInRound`, performing no staleness or validity checks on the returned price. When a Chainlink LST/ETH feed goes stale while the actual market price has dropped, an attacker can deposit the LST at the inflated stale price via `LRTDepositPool.depositAsset()` and receive more rsETH than the deposited value warrants, directly reducing the ETH backing per rsETH for all existing holders.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` at line 52 reads:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
```

All validity fields (`roundId`, `updatedAt`, `answeredInRound`) are silently discarded. No check is made that `answeredInRound >= roundId`, `updatedAt != 0`, `price > 0`, or that `updatedAt` falls within an acceptable heartbeat window. [1](#0-0) 

This oracle is consumed by `LRTDepositPool.getRsETHAmountToMint()`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [2](#0-1) 

And by `LRTOracle._getTotalEthInProtocol()`, which computes the denominator `rsETHPrice`: [3](#0-2) 

The same codebase already implements all required checks in `ChainlinkOracleForRSETHPoolCollateral.getRate()`: [4](#0-3) 

`ChainlinkPriceOracle` — the oracle used for all LST assets in the core deposit path — has none of these guards, creating an inconsistency that leaves the primary deposit flow unprotected.

## Impact Explanation
When a Chainlink LST/ETH feed goes stale at an inflated price while the actual market price has dropped (depeg, slashing, sequencer downtime), `getRsETHAmountToMint` computes rsETH to mint using the stale high price. The attacker receives rsETH representing more ETH value than they deposited. The shortfall is borne by all existing rsETH holders whose tokens are now backed by less ETH per unit — a direct reduction in the at-rest value of their holdings.

**Impact: Critical** — Direct theft of user funds at-rest. The backing ETH per rsETH is permanently reduced for all existing holders proportional to the price discrepancy and deposit size. At sufficient scale (large deposit during a significant depeg), this can approach protocol insolvency, which is also a listed Critical impact.

## Likelihood Explanation
Chainlink feeds go stale in documented real-world scenarios: network congestion, sequencer downtime on L2s (Arbitrum, Base, Optimism — chains where RSETHPool contracts are deployed), feed deprecation windows, and extreme market volatility. An attacker only needs to monitor the `updatedAt` timestamp of the relevant feed and call `depositAsset()` during the stale window. No special permissions, flashloans, or complex setup are required — the exploit path is a single standard public call.

**Likelihood: Medium** — Stale feed windows are infrequent but historically documented; the attacker path is trivially simple once the condition is met.

## Recommendation
Add staleness and validity checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    // Optional: if (block.timestamp - updatedAt > HEARTBEAT_THRESHOLD) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

## Proof of Concept
1. Chainlink's `stETH/ETH` feed goes stale at `1.0e18` while stETH depegs to `0.95e18` on the market.
2. Attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, 0, "")`.
3. `_beforeDeposit` → `getRsETHAmountToMint` → `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns stale `1.0e18`.
4. `rsethAmountToMint = (1000e18 * 1.0e18) / rsETHPrice` — computed at full peg.
5. Attacker receives rsETH worth ~1000 ETH while depositing stETH worth only ~950 ETH.
6. The ~50 ETH difference is extracted from existing rsETH holders' backing.

**Foundry fork test plan:**
- Fork mainnet/Ethereum at a block where a Chainlink LST feed is known to be stale (or mock `latestRoundData` to return an outdated `updatedAt`).
- Record `rsETHPrice()` before the deposit.
- Execute `depositAsset(stETH, largeAmount, 0, "")` as an unprivileged address.
- Assert that `rsETHPrice()` after the deposit is lower than before, confirming dilution of existing holders.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
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
