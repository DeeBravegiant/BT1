### Title
Missing Staleness Check in `ChainlinkPriceOracle.getAssetPrice()` Allows Stale Price to Drive Incorrect rsETH Minting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards all return values except `price`. No `updatedAt` heartbeat check and no `answeredInRound` validation are performed. This is the same class of oracle integration omission as the reference report's hardcoded-parameter bugs: a required integration parameter is simply not used, causing the function to silently operate on incorrect data.

### Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

`updatedAt`, `answeredInRound`, and `roundId` are all discarded. There is no check of the form `block.timestamp - updatedAt > heartbeat` and no `answeredInRound >= roundId` guard. [1](#0-0) 

This price is consumed by `LRTOracle.getAssetPrice()`, which is called inside `_getTotalEthInProtocol()`, which in turn drives `_updateRsETHPrice()`. [2](#0-1) 

`_updateRsETHPrice()` computes `newRsETHPrice = (totalETHInProtocol - protocolFeeInETH) / rsethSupply` and stores it as the canonical rsETH price used by every L2 pool oracle. [3](#0-2) 

By contrast, `ChainlinkOracleForRSETHPoolCollateral` — used for pool collateral tokens — does perform `answeredInRound < roundID` and `timestamp == 0` checks, but still omits a time-based heartbeat check and is not used for the main asset pricing path. [4](#0-3) 

### Impact Explanation
If a Chainlink feed goes stale (e.g., during L1 network congestion, a sequencer outage on an L2 where the feed is mirrored, or a feed that simply stops updating), `getAssetPrice()` returns the last known price without any revert. Two concrete consequences:

1. **Stale-high price** (asset dropped in value, feed not yet updated): `_getTotalEthInProtocol()` overstates TVL → `newRsETHPrice` is inflated → depositors receive fewer rsETH tokens than they are entitled to. Existing holders are silently enriched at depositors' expense.

2. **Stale-low price** (asset rose in value, feed not yet updated): TVL is understated → `newRsETHPrice` falls below `highestRsethPrice` → if the gap exceeds `pricePercentageLimit`, `_updateRsETHPrice()` pauses both `LRTDepositPool` and `LRTWithdrawalManager`, temporarily freezing all deposits and withdrawals for all users. [5](#0-4) 

Impact classification: **Medium — Temporary freezing of funds** (scenario 2) / **Low — Contract fails to deliver promised returns** (scenario 1).

### Likelihood Explanation
`updateRSETHPrice()` is a public, permissionless function callable by anyone. [6](#0-5) 

An unprivileged caller can invoke it at any time, including during a period when a Chainlink feed is stale. Chainlink feeds on Ethereum mainnet have heartbeats of 1–24 hours depending on the asset; a feed that has not updated within its heartbeat window is a realistic condition during network stress. No special access is required to trigger the vulnerable path.

### Recommendation
Add a heartbeat staleness check in `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();
require(answeredInRound >= roundId, "Stale price");
require(block.timestamp - updatedAt <= MAX_HEARTBEAT, "Price too old");
require(price > 0, "Invalid price");
```

`MAX_HEARTBEAT` should be set per-feed (e.g., 3600 s for 1-hour feeds, 90000 s for 24-hour feeds). Alternatively, store a per-asset heartbeat mapping alongside `assetPriceFeed`.

### Proof of Concept

1. Assume `stETH/ETH` Chainlink feed has a 24-hour heartbeat and last updated 23 hours ago at price `1.05 ETH`.
2. The actual stETH price drops to `0.95 ETH` due to a slashing event, but the feed has not yet updated.
3. Anyone calls `LRTOracle.updateRSETHPrice()`.
4. `ChainlinkPriceOracle.getAssetPrice(stETH)` returns `1.05e18` (stale).
5. `_getTotalEthInProtocol()` overstates TVL by ~10%.
6. `newRsETHPrice` is inflated; new depositors receive fewer rsETH tokens than the true exchange rate warrants.
7. Alternatively, if the stale price is lower than the true price and the gap exceeds `pricePercentageLimit`, the protocol auto-pauses, freezing all user deposits and withdrawals until an admin manually unpauses. [7](#0-6)

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

**File:** contracts/LRTOracle.sol (L214-232)
```text
    function _updateRsETHPrice() internal {
        address rsETHTokenAddress = lrtConfig.rsETH();
        uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();

        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }

        if (highestRsethPrice == 0) {
            highestRsethPrice = rsETHPrice;
        }

        uint256 previousPrice = rsETHPrice;

        // get total ETH in the protocol (normalized to 1e18)
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

```

**File:** contracts/LRTOracle.sol (L249-251)
```text
        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

```

**File:** contracts/LRTOracle.sol (L270-282)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
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
