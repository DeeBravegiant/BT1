### Title
Stale Chainlink Price Accepted Without Staleness Check Enables Excess rsETH Minting at Inflated LST Price - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards every staleness indicator returned by the feed (`updatedAt`, `answeredInRound`, `roundId`). If a Chainlink LST/ETH feed stops updating — due to a heartbeat miss, sequencer downtime, or oracle disruption — the last stale price is returned without any revert. Because `LRTDepositPool.depositAsset()` uses this price to compute how many rsETH tokens to mint, an attacker can deposit an LST at an inflated stale price and receive more rsETH than the asset is actually worth, diluting all existing rsETH holders.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink round data as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

All five return values (`roundId`, `answer`, `startedAt`, `updatedAt`, `answeredInRound`) are captured in the destructuring, but only `price` (`answer`) is used. The fields `updatedAt` and `answeredInRound` — which are the standard Chainlink staleness signals — are discarded without any check. There is no comparison of `block.timestamp - updatedAt` against a heartbeat threshold, and no check that `answeredInRound >= roundId`.

This oracle is the price source for all LST assets (stETH, cbETH, rETH, etc.) registered in the protocol. It is consumed by `LRTOracle.getAssetPrice()`:

```solidity
return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
``` [2](#0-1) 

Which is in turn consumed by `LRTDepositPool.getRsETHAmountToMint()` to determine how many rsETH tokens to mint per deposit:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

This value is computed inside `_beforeDeposit`, which is called by the public, permissionless `depositAsset()` function: [4](#0-3) 

The analog to the Olympus M-31 finding is exact: in Olympus, `beat()` not being called left the RBS price stale while swaps continued at the old price. Here, a Chainlink heartbeat miss leaves the LST/ETH price stale while deposits continue at the old price. In both cases, the critical operation (swap / mint) proceeds without verifying that the price data is current.

For contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` — a different oracle wrapper in the same repo — does perform partial staleness checks (`answeredInRound < roundID`, `timestamp == 0`), demonstrating that the protocol is aware of the pattern but failed to apply it to the primary deposit oracle: [5](#0-4) 

---

### Impact Explanation

If a Chainlink LST/ETH feed goes stale at a price higher than the true market price (e.g., stETH/ETH feed last reported 1.05 but the real price has dropped to 0.95 due to a depeg), an attacker can:

1. Deposit stETH into `LRTDepositPool.depositAsset()`.
2. Receive rsETH calculated at the inflated stale rate (1.05 ETH per stETH instead of 0.95).
3. Immediately sell the excess rsETH on a secondary market at the fair price.

The attacker extracts value that belongs to existing rsETH holders, who now hold a diluted share of the protocol's TVL. This constitutes **theft of value from existing rsETH holders** — matching the "theft of unclaimed yield / dilution" impact class. In a severe depeg scenario the magnitude can be large and the attack is repeatable until the feed resumes or the protocol is paused.

**Impact: High** — Theft of value from existing rsETH holders via excess rsETH minting at a stale inflated LST price.

---

### Likelihood Explanation

Chainlink feed staleness is a well-documented, real-world event. Feeds have heartbeat windows (typically 1 hour for LST/ETH pairs on mainnet). A heartbeat miss, a sequencer outage (on L2), or a Chainlink node disruption during a volatile market event (exactly when LST depegs are most likely) creates the window. The attacker entry path is the public, permissionless `depositAsset()` function — no special role or privilege is required. The attack is straightforward and can be executed by any on-chain bot monitoring feed staleness.

**Likelihood: Medium** — Requires a Chainlink feed to go stale, which is an uncommon but historically observed event, especially during market stress.

---

### Recommendation

Add staleness validation inside `ChainlinkPriceOracle.getAssetPrice()`. Store a per-feed `heartbeat` mapping and revert if the price is outdated:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (block.timestamp - updatedAt > heartbeat[assetPriceFeed[asset]]) revert StalePrice();
    if (price <= 0) revert InvalidPrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

The `heartbeat` value should be set per feed (e.g., 3600 seconds for a 1-hour heartbeat feed, with a small buffer). This mirrors the pattern already partially applied in `ChainlinkOracleForRSETHPoolCollateral`.

---

### Proof of Concept

1. stETH/ETH Chainlink feed last updated at price `1.05e18` (stETH trading at a premium).
2. A market event causes stETH to depeg; real price falls to `0.95e18`.
3. The Chainlink feed heartbeat is missed; `updatedAt` is now `> 1 hour` old, but `latestRoundData()` still returns `1.05e18`.
4. Attacker calls `LRTDepositPool.depositAsset(stETH, 1e18, 0, "")`.
5. `getRsETHAmountToMint` computes: `rsethAmountToMint = (1e18 * 1.05e18) / rsETHPrice`. With a fair `rsETHPrice` of `1e18`, the attacker receives `1.05e18` rsETH for `1e18` stETH worth only `0.95e18` ETH.
6. Attacker sells `1.05e18` rsETH on a secondary market at the fair price (`~1e18` ETH per rsETH), receiving `~1.05 ETH` for an asset worth `0.95 ETH` — a `~0.10 ETH` profit per stETH deposited, extracted from existing rsETH holders.
7. The attack is repeatable until the feed resumes or the protocol is manually paused. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
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

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L26-36)
```text
    function getRate() public view returns (uint256) {
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
```
