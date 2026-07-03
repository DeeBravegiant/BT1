### Title
Missing Chainlink `latestRoundData()` Return Value Validation Enables Stale Price Exploitation - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards `roundId`, `updatedAt`, and `answeredInRound`, accepting any price — including stale, zero, or incomplete-round values — without validation. This is the direct EVM analog to the reported Pyth issue: just as the Pyth `PythPrice` struct was missing the confidence interval and exponent needed to safely interpret a price, `ChainlinkPriceOracle` discards the fields needed to verify price freshness and completeness. The same protocol already implements correct validation in `ChainlinkOracleForRSETHPoolCollateral`, making the omission in the core oracle inconsistent and exploitable.

### Finding Description
In `contracts/oracles/ChainlinkPriceOracle.sol`, `getAssetPrice()` reads only the `answer` field from `latestRoundData()`:

```solidity
// ChainlinkPriceOracle.sol line 52
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

The three discarded fields carry safety-critical information:
- `answeredInRound` vs `roundId`: detects a stale answer carried over from a prior round
- `updatedAt` (`timestamp`): detects an incomplete or in-progress round (value = 0)
- sign of `answer`: a non-positive price is invalid

By contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` — used in the pool collateral path — validates all three:

```solidity
// ChainlinkOracleForRSETHPoolCollateral.sol lines 27-32
(uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
    AggregatorV3Interface(oracle).latestRoundData();
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

`ChainlinkPriceOracle` is the oracle adapter registered in `LRTOracle` for LST assets (stETH, rETH, etc.). Its output feeds two critical paths:

**Path 1 — rsETH mint rate** (`LRTDepositPool.getRsETHAmountToMint`):
```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

**Path 2 — rsETH price update** (`LRTOracle._updateRsETHPrice` → `_getTotalEthInProtocol`): [4](#0-3) 

### Impact Explanation
**High — Theft of unclaimed yield / share mis-accounting.**

When a Chainlink feed enters a stale state (e.g., during high network congestion or a sequencer outage on L1), the last reported price is frozen. If that frozen price is *above* the true current value of an LST (e.g., stETH before a depeg event, or simply a feed that has not heartbeat-updated):

1. `getAssetPrice(stETH)` returns the stale inflated price.
2. `getRsETHAmountToMint` computes `(amount × stale_high_price) / rsETHPrice`, minting more rsETH than the deposited asset is worth.
3. The attacker redeems the excess rsETH, extracting value from all existing rsETH holders.

Conversely, a stale *low* price (e.g., zero from an incomplete round) causes `_updateRsETHPrice` to compute a deflated `totalETHInProtocol`, crashing the rsETH price and triggering the downside-protection pause — a temporary freeze of all deposits and withdrawals.

### Likelihood Explanation
Chainlink feeds have documented heartbeat intervals (e.g., 1 hour for ETH/USD, 24 hours for some LST feeds). During periods of low volatility, feeds may not update for the full heartbeat window. An attacker monitoring on-chain feed timestamps can identify the stale window and act within it. No privileged access is required; `depositAsset` on `LRTDepositPool` is open to any user.

### Recommendation
Apply the same validation pattern already used in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();
require(answeredInRound >= roundId, "Stale price");
require(updatedAt != 0, "Incomplete round");
require(price > 0, "Invalid price");
```

Additionally, add a configurable `stalePriceThreshold` (e.g., `block.timestamp - updatedAt <= maxStaleness`) to bound the acceptable age of a price.

### Proof of Concept
1. Observe that a Chainlink LST/ETH feed (e.g., stETH/ETH) has not updated for its full heartbeat window — `updatedAt` is stale but `answeredInRound == roundId` (no revert condition exists in `ChainlinkPriceOracle`).
2. The stale price is, say, 1.002 ETH/stETH while the true market rate has dropped to 0.998 ETH/stETH.
3. Attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18)`.
4. `getRsETHAmountToMint` calls `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns `1.002e18` (stale).
5. rsETH minted = `(1000e18 × 1.002e18) / rsETHPrice` — attacker receives ~0.4% more rsETH than fair value.
6. Attacker initiates withdrawal, redeeming the excess rsETH for ETH, extracting value from all existing rsETH holders proportional to the price deviation and deposit size. [1](#0-0) [5](#0-4) [6](#0-5)

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

**File:** contracts/LRTDepositPool.sol (L515-521)
```text
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

**File:** contracts/LRTOracle.sol (L230-232)
```text
        // get total ETH in the protocol (normalized to 1e18)
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

```
