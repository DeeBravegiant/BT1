### Title
No Staleness Check on Chainlink `latestRoundData()` Enables Stale-Price Minting of rsETH - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls Chainlink's `latestRoundData()` but discards every return value except `price`. There is no check on `updatedAt`, `answeredInRound`, or `roundId`. A stale (inflated) price for any supported LST asset causes `LRTDepositPool.getRsETHAmountToMint()` to mint more rsETH than the depositor's assets are worth, diluting all existing rsETH holders.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

All five return values are destructured, but only `price` is used. The fields `roundId`, `startedAt`, `updatedAt`, and `answeredInRound` are silently discarded. No staleness guard of any kind is applied — no `block.timestamp - updatedAt < heartbeat` check, no `answeredInRound >= roundId` check, and no `updatedAt > 0` check.

This price is consumed directly in `LRTDepositPool.getRsETHAmountToMint()`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [2](#0-1) 

`lrtOracle.getAssetPrice(asset)` delegates to the registered `IPriceFetcher`, which for Chainlink-backed assets is `ChainlinkPriceOracle`: [3](#0-2) 

The minting path is fully reachable by any unprivileged user via `depositAsset()` or `depositETH()`: [4](#0-3) 

For contrast, the pool-side oracle wrapper `ChainlinkOracleForRSETHPoolCollateral` does apply partial staleness guards (`answeredInRound < roundID`, `timestamp == 0`), but `ChainlinkPriceOracle` — the oracle used for the core deposit/mint path — applies none at all. [5](#0-4) 

---

### Impact Explanation

If a Chainlink feed for a supported LST (e.g., stETH/ETH, ETHx/ETH) becomes stale and its last reported price is higher than the true current price, `getAssetPrice()` returns an inflated value. The minting formula then produces more rsETH than the deposited assets are worth. When the attacker later redeems, they extract more ETH than they deposited, at the direct expense of all other rsETH holders whose share of the pool is diluted. This constitutes **theft of funds from existing rsETH holders** (at minimum, theft of unclaimed yield; at maximum, direct theft of principal depending on the magnitude of the price deviation).

**Impact: High — Theft of unclaimed yield / dilution of existing rsETH holders.**

---

### Likelihood Explanation

Chainlink feeds go stale in realistic, non-adversarial conditions: network congestion preventing keeper transactions, sequencer downtime on L2 deployments, or a feed simply not updating because the deviation threshold was not crossed while the true price moved. An attacker monitoring mempool or on-chain oracle state can detect a stale feed and immediately call `depositAsset()` before the feed recovers. No privileged access is required.

**Likelihood: Medium** — requires a stale feed window, which occurs periodically in practice.

---

### Recommendation

Add staleness validation in `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

require(answeredInRound >= roundId, "Stale price: answeredInRound < roundId");
require(updatedAt > 0, "Stale price: incomplete round");
require(block.timestamp - updatedAt <= MAX_PRICE_AGE, "Stale price: too old");
require(price > 0, "Invalid price");
```

`MAX_PRICE_AGE` should be set per feed based on its documented heartbeat (e.g., 3600 s for a 1-hour heartbeat feed, with a small buffer).

---

### Proof of Concept

1. Chainlink's stETH/ETH feed last updated at `T - 2h` with price `1.05 ETH`. True current price is `1.00 ETH` (feed is stale and inflated).
2. Attacker calls `LRTDepositPool.depositAsset(stETH, 100e18, 0, "")`.
3. `getRsETHAmountToMint(stETH, 100e18)` computes:
   - `getAssetPrice(stETH)` → returns stale `1.05e18` from `ChainlinkPriceOracle`
   - `rsETHPrice` → e.g., `1.02e18` (current stored price)
   - `rsethAmountToMint = (100e18 * 1.05e18) / 1.02e18 ≈ 102.94 rsETH`
4. Fair value at true price: `(100e18 * 1.00e18) / 1.02e18 ≈ 98.04 rsETH`
5. Attacker receives ~4.9 extra rsETH (~5% excess) at the expense of existing holders.
6. After the feed updates, attacker redeems 102.94 rsETH for ~105 ETH, having deposited 100 ETH worth of stETH — a ~5 ETH profit extracted from the pool. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTDepositPool.sol (L99-117)
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

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L27-32)
```text
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();
```
