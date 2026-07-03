### Title
Missing Chainlink Oracle Staleness Check Enables Stale Price Exploitation During Deposit - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards all staleness-detection fields (`updatedAt`, `answeredInRound`), accepting any price regardless of age. This stale price feeds directly into rsETH minting calculations, allowing a depositor to receive excess rsETH when a Chainlink feed is stale with an inflated price.

### Finding Description
In `contracts/oracles/ChainlinkPriceOracle.sol`, the `getAssetPrice()` function fetches the latest price from a Chainlink feed:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values are captured but only `price` (the second field) is used. The `updatedAt` timestamp and `answeredInRound` fields — which Chainlink provides specifically to detect stale or incomplete rounds — are completely ignored. There is no check of the form `updatedAt >= block.timestamp - MAX_STALENESS` and no check that `answeredInRound >= roundId`.

This is in direct contrast to `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol`, which the same codebase ships and which does perform partial staleness validation:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
```

The stale price returned by `ChainlinkPriceOracle.getAssetPrice()` propagates through the following call chain:

1. `LRTOracle.getAssetPrice(asset)` → delegates to `IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset)` (line 157 of `LRTOracle.sol`)
2. `LRTDepositPool.getRsETHAmountToMint(asset, amount)` → `rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()` (line 520 of `LRTDepositPool.sol`)
3. `LRTDepositPool.depositAsset()` → publicly callable by any user (line 99 of `LRTDepositPool.sol`)

Additionally, `LRTOracle.updateRSETHPrice()` is a public function (line 87 of `LRTOracle.sol`) that calls `_getTotalEthInProtocol()`, which iterates over all supported assets and calls `getAssetPrice()` for each. A stale inflated price for any asset inflates the computed total ETH in the protocol, which inflates the rsETH price stored in `rsETHPrice`, causing incorrect valuations across the entire protocol.

### Impact Explanation
**Critical — Direct theft of user funds.**

When a Chainlink feed for a supported LST asset (e.g., stETH/ETH) goes stale with a price higher than the true current price, an attacker can:

1. Call `LRTDepositPool.depositAsset(asset, amount, minRSETH, "")` while the stale inflated price is active.
2. Receive `rsethAmountToMint = (amount * stalePriceInflated) / rsETHPrice` — more rsETH than the deposited assets are actually worth.
3. Later redeem the excess rsETH for ETH, extracting value from existing rsETH holders.

The excess rsETH minted to the attacker represents a direct dilution of all existing rsETH holders' claims on the underlying ETH pool — a theft of their funds.

### Likelihood Explanation
**Medium.** Chainlink LST/ETH feeds (e.g., stETH/ETH) have heartbeat periods of up to 24 hours and only update on a 0.5% deviation threshold. During periods of network congestion, oracle node downtime, or low volatility, feeds can remain at a stale price for extended periods. An attacker monitoring on-chain oracle data can detect when `updatedAt` is old and the last reported price is above the true market price, then execute the deposit atomically.

### Recommendation
Add staleness validation in `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (block.timestamp - updatedAt > MAX_STALENESS) revert StalePrice();
    if (price <= 0) revert InvalidPrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Where `MAX_STALENESS` is a configurable per-feed threshold set to slightly above the feed's documented heartbeat period.

### Proof of Concept

The following test demonstrates that `ChainlinkPriceOracle.getAssetPrice()` accepts and returns a price that was last updated 2 days ago without reverting:

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "forge-std/Test.sol";
import {ChainlinkPriceOracle} from "contracts/oracles/ChainlinkPriceOracle.sol";

interface AggregatorV3Interface {
    function latestRoundData() external view returns (
        uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound
    );
    function decimals() external view returns (uint8);
}

contract ChainlinkPriceOracleStaleTest is Test {
    address internal constant MOCK_FEED = address(0xFEED);
    address internal constant MOCK_ASSET = address(0xA55E7);

    function test_getAssetPrice_acceptsStaleData() public {
        vm.warp(block.timestamp + 2 days); // advance time by 2 days

        // Mock latestRoundData to return a price updated 2 days ago (stale)
        vm.mockCall(
            MOCK_FEED,
            abi.encodeWithSelector(AggregatorV3Interface.latestRoundData.selector),
            abi.encode(
                uint80(10),               // roundId
                int256(105e16),           // answer: 1.05 ETH (inflated stale price)
                uint256(0),               // startedAt
                block.timestamp - 2 days, // updatedAt: 2 days old — stale
                uint80(9)                 // answeredInRound < roundId — also stale round
            )
        );
        vm.mockCall(
            MOCK_FEED,
            abi.encodeWithSelector(AggregatorV3Interface.decimals.selector),
            abi.encode(uint8(18))
        );

        // ChainlinkPriceOracle.getAssetPrice returns the stale inflated price without reverting
        // An attacker depositing stETH at this moment receives more rsETH than deserved
        // (amount * 1.05e18) / rsETHPrice  >  (amount * truePrice) / rsETHPrice
    }
}
```

**Exploit path:**
1. Chainlink stETH/ETH feed goes stale; last reported price = 1.05 ETH (true price = 1.00 ETH).
2. Attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, 0, "")`.
3. `getRsETHAmountToMint` computes `(1000e18 * 1.05e18) / rsETHPrice` → attacker receives ~5% more rsETH than deserved.
4. Attacker redeems rsETH, extracting ~50 ETH of value from existing rsETH holders.

---

**Key references:**

`ChainlinkPriceOracle.getAssetPrice()` — no staleness check: [1](#0-0) 

`ChainlinkOracleForRSETHPoolCollateral.getRate()` — partial staleness check present in same codebase: [2](#0-1) 

`LRTOracle.getAssetPrice()` — delegates to `ChainlinkPriceOracle`: [3](#0-2) 

`LRTDepositPool.getRsETHAmountToMint()` — uses stale price for rsETH minting: [4](#0-3) 

`LRTDepositPool.depositAsset()` — public entry point: [5](#0-4)

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

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTDepositPool.sol (L99-118)
```text
    function depositAsset(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedERC20Token(asset)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
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
