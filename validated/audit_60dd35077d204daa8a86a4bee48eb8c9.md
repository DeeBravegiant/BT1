Audit Report

## Title
Chainlink Oracle Accepts Stale/Invalid Price Data Without Staleness or Round Completeness Checks - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all return values except `price`, with no staleness, round-completeness, or price-validity checks. A stale or zero price propagates directly into rsETH minting calculations and the protocol-wide `rsETHPrice` update, enabling either incorrect rsETH issuance or an auto-triggered protocol freeze.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` reads the Chainlink feed as:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

All five return values — `roundId`, `price`, `startedAt`, `updatedAt`, `answeredInRound` — are available but only `price` is used. The following checks are entirely absent:

- **No heartbeat/staleness check**: `updatedAt` is ignored; a feed that stopped updating hours ago is silently accepted.
- **No round-completeness check**: `startedAt` is never verified to be non-zero.
- **No stale-round check**: `answeredInRound >= roundId` is never verified.
- **No price-validity check**: `price` is never checked to be `> 0`; a zero price returns `0` from `getAssetPrice`.

By contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` in the same repository correctly implements all three guards:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

`ChainlinkPriceOracle` is the oracle used for all LST assets registered in `LRTOracle`. The stale price flows through two critical paths:

**Path 1 — rsETH minting:**
`LRTDepositPool.getRsETHAmountToMint()` computes:
```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

A stale inflated price causes depositors to receive more rsETH than the deposited asset is worth, diluting existing holders.

**Path 2 — Protocol-wide rsETH price update:**
`_getTotalEthInProtocol()` calls `getAssetPrice(asset)` for every supported asset: [4](#0-3) 

If a stale/zero price causes `totalETHInProtocol` to drop below `highestRsethPrice` by more than `pricePercentageLimit`, `_updateRsETHPrice()` auto-pauses `lrtDepositPool`, `withdrawalManager`, and `LRTOracle`: [5](#0-4) 

`updateRSETHPrice()` is a public function callable by any unprivileged user: [6](#0-5) 

## Impact Explanation
Two concrete allowed impacts are demonstrated:

1. **High — Theft of unclaimed yield**: A stale inflated price (e.g., stETH feed frozen before a depeg) causes new depositors to receive excess rsETH, diluting the yield accrued by existing rsETH holders. This is a direct, quantifiable transfer of value from existing holders to new depositors.

2. **Medium — Temporary freezing of funds**: A stale deflated price or a zero price (from an incomplete round where `startedAt == 0`) causes `_getTotalEthInProtocol` to undervalue TVL, triggering the auto-pause and freezing all deposits and withdrawals until an admin manually unpauses.

## Likelihood Explanation
Chainlink feeds can go stale during network congestion, node outages, or aggregator issues — all documented failure modes. `updateRSETHPrice()` is public and callable by any address at any time, including during a stale-feed window. No special privileges or victim mistakes are required. The deployed `ChainlinkPriceOracle` at `0x78C12ccE8346B936117655Dd3D70a2501Fd3d6e6` is live on mainnet for all LST assets.

## Recommendation
Apply the same guards already present in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    require(price > 0, "invalid price");
    require(startedAt > 0, "round not complete");
    require(answeredInRound >= roundId, "stale price");
    require(block.timestamp - updatedAt <= heartbeat[asset], "stale price");

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Add a per-asset `heartbeat` mapping set to the Chainlink feed's documented heartbeat (e.g., 3600 seconds for most ETH-denominated LST feeds).

## Proof of Concept

**Freeze path (zero price):**
1. A Chainlink feed returns `price == 0` (incomplete round, `startedAt == 0`).
2. Any caller invokes `LRTOracle.updateRSETHPrice()`.
3. `_getTotalEthInProtocol()` calls `getAssetPrice(asset)` → `ChainlinkPriceOracle.getAssetPrice()` → returns `0`.
4. `totalETHInProtocol` is severely undervalued; `newRsETHPrice` drops far below `highestRsethPrice`.
5. `isPriceDecreaseOffLimit` is true; `lrtDepositPool.pause()`, `withdrawalManager.pause()`, and `_pause()` are called.
6. All deposits and withdrawals are frozen until admin manually unpauses.

**Theft-of-yield path (stale inflated price):**
1. Chainlink's stETH/ETH feed stops updating; `updatedAt` is 2+ hours old but price is stale-high (e.g., 1.05 ETH before a depeg to 0.95 ETH).
2. Any user calls `LRTDepositPool.depositAsset(stETH, amount, 0, "")`.
3. `getRsETHAmountToMint` → `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` returns `1.05e18`.
4. `rsethAmountToMint = (amount * 1.05e18) / rsETHPrice` — depositor receives ~10.5% more rsETH than the asset is worth.
5. Existing rsETH holders are diluted by the excess minting.

**Foundry fork test plan:** Fork mainnet, mock the stETH/ETH Chainlink aggregator to return a stale `updatedAt` and `price == 0`, call `updateRSETHPrice()` as an unprivileged EOA, and assert that `LRTDepositPool.paused() == true`.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L30-32)
```text
        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L270-281)
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
```

**File:** contracts/LRTOracle.sol (L336-343)
```text
        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```
