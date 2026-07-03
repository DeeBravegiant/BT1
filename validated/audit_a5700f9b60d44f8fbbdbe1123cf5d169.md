Audit Report

## Title
Missing Chainlink Price Feed Staleness Check Enables Inflated rsETH Minting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all return values except `price`, performing no staleness validation. A stale LST/ETH price is silently accepted and propagated into the rsETH minting calculation, allowing a depositor to receive more rsETH than the real value of their collateral warrants, diluting existing rsETH holders.

## Finding Description
In `contracts/oracles/ChainlinkPriceOracle.sol` at line 52, `getAssetPrice()` fetches the Chainlink price as:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

The `updatedAt` timestamp and `answeredInRound`/`roundId` fields are completely ignored. [1](#0-0) 

This directly contrasts with `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol`, which checks `answeredInRound < roundID` and `timestamp == 0` before accepting a price — demonstrating the protocol is aware of the required validation pattern. [2](#0-1) 

`LRTOracle.getAssetPrice()` delegates directly to the registered `IPriceFetcher`, which for Chainlink-backed LSTs is `ChainlinkPriceOracle`. [3](#0-2) 

The stale price is consumed in two critical paths:

1. **Minting path** — `LRTDepositPool.getRsETHAmountToMint()` computes `(amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()`. [4](#0-3) 

2. **rsETH price update path** — `LRTOracle._getTotalEthInProtocol()` multiplies each asset's total deposit amount by `getAssetPrice(asset)` to compute protocol TVL, which then sets `rsETHPrice`. [5](#0-4) 

No existing guard in `depositAsset` or `_beforeDeposit` checks oracle freshness; the only check is a `minRSETHAmountExpected` slippage guard, which an attacker would set to benefit from the inflated price. [6](#0-5) 

## Impact Explanation
When a Chainlink LST/ETH feed goes stale with a last-reported price above the real current price, any depositor receives more rsETH than the ETH value of their collateral justifies. This excess rsETH represents a larger proportional claim on the protocol's TVL, directly diluting the yield accrued to all existing rsETH holders. This constitutes **theft of unclaimed yield** — a High-severity impact under the allowed scope.

## Likelihood Explanation
Chainlink feeds have documented heartbeat intervals (e.g., 1 hour for ETH/stETH). During L1 congestion or Chainlink infrastructure incidents, feeds can lag beyond their heartbeat. The `depositAsset` function is permissionless and callable by any address. The attacker only needs to monitor the on-chain `updatedAt` value returned by `latestRoundData()` and act when it exceeds the heartbeat threshold — no privileged access, governance capture, or oracle operator compromise is required.

## Recommendation
Add a time-based staleness check in `ChainlinkPriceOracle.getAssetPrice()`, consistent with the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

require(answeredInRound >= roundId, "Stale round");
require(updatedAt != 0, "Incomplete round");
require(block.timestamp - updatedAt <= STALENESS_THRESHOLD, "Stale price");
require(price > 0, "Invalid price");
```

`STALENESS_THRESHOLD` should be set per feed based on its documented heartbeat (e.g., 3600 seconds for a 1-hour heartbeat feed, with a reasonable buffer).

## Proof of Concept
1. Assume `stETH/ETH` Chainlink feed last updated 2 hours ago at `1.05e18` (real current value: `1.02e18`).
2. Attacker calls `LRTDepositPool.depositAsset(stETH, 100e18, 0, "")`.
3. `_beforeDeposit` → `getRsETHAmountToMint` → `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns stale `1.05e18`.
4. rsETH minted = `100e18 * 1.05e18 / rsETHPrice` — approximately 2.9% more rsETH than the real collateral value justifies.
5. Attacker holds excess rsETH representing a larger claim on protocol TVL, diluting all existing rsETH holders' proportional share of accrued yield.

**Foundry fork test plan:** Fork mainnet, mock `latestRoundData()` on the stETH/ETH feed to return a price with `updatedAt = block.timestamp - 7200` (2 hours stale), call `depositAsset`, and assert that `rsethAmountToMint` exceeds the amount that would be minted with a fresh price at the real current rate. Compare against a deposit using `ChainlinkOracleForRSETHPoolCollateral` (which would revert on `answeredInRound < roundID`) to demonstrate the inconsistency.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L27-32)
```text
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L339-343)
```text
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

**File:** contracts/LRTDepositPool.sol (L648-670)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
    }
```
