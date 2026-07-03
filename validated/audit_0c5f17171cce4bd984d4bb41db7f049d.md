### Title
Stale Chainlink Price Accepted Without Staleness Validation Enables Over-Minting of rsETH - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all validation fields (`updatedAt`, `answeredInRound`). A stale or frozen Chainlink feed for any supported LST asset allows a depositor to mint rsETH at an inflated price, stealing value from existing rsETH holders.

### Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price but silently ignores the staleness and round-completeness fields returned by `latestRoundData()`:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol:52
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

The destructured return ignores `roundId`, `startedAt`, `updatedAt`, and `answeredInRound`. No check is made that `updatedAt != 0`, that `answeredInRound >= roundId`, or that `block.timestamp - updatedAt` is within an acceptable heartbeat window.

This contrasts directly with `ChainlinkOracleForRSETHPoolCollateral.sol`, the L2 pool's own Chainlink wrapper, which performs all three checks:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol:30-32
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The stale price from `ChainlinkPriceOracle` propagates through the following call chain on L1:

1. `LRTOracle.getAssetPrice(asset)` → delegates to `IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset)` [1](#0-0) 
2. `LRTDepositPool.getRsETHAmountToMint(asset, amount)` → `(amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()` [2](#0-1) 
3. `LRTDepositPool.depositAsset(asset, depositAmount, ...)` → calls `_beforeDeposit` → `getRsETHAmountToMint` → mints rsETH [3](#0-2) 

### Impact Explanation
If a supported LST (e.g., stETH, rETH, sfrxETH) depegs while its Chainlink feed is stale at the pre-depeg price, a depositor receives rsETH calculated against the inflated stale price rather than the true current price. The over-minted rsETH represents a direct dilution of all existing rsETH holders' claims on the underlying ETH pool — equivalent to theft of yield from existing holders. At sufficient scale (e.g., a 10% depeg on a large deposit), this constitutes protocol insolvency.

**Impact: High — Theft of unclaimed yield / potential protocol insolvency.**

### Likelihood Explanation
Chainlink feeds go stale during: L1 network congestion preventing keeper transactions, a feed being deprecated or migrated, or a heartbeat miss during low-volatility periods. The protocol supports multiple LST assets, each with its own feed, multiplying the attack surface. An attacker can monitor on-chain feed `updatedAt` timestamps and time a large deposit precisely when a feed is stale. No admin action is required; the path is fully permissionless via `depositAsset`.

**Likelihood: Medium.**

### Recommendation
Add staleness and round-completeness checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    // Optional: if (block.timestamp - updatedAt > MAX_STALENESS) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

### Proof of Concept
**Setup:** stETH/ETH Chainlink feed last updated 4 hours ago at `1.0e18`. stETH has since depegged to `0.9e18` on-chain. Current rsETH price = `1.05e18`.

**Attack:**
1. Attacker observes `updatedAt` is stale on the stETH feed.
2. Attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, 0, "")`.
3. `getRsETHAmountToMint` computes: `1000e18 * 1.0e18 / 1.05e18 = 952.38e18 rsETH`.
4. Actual ETH value deposited: `1000 * 0.9 = 900 ETH`.
5. Fair rsETH for 900 ETH at 1.05 price: `900 / 1.05 = 857.14 rsETH`.
6. Attacker receives `952.38 rsETH` instead of `857.14 rsETH` — **~95 rsETH over-minted (~$190k at $2000/ETH)** — extracted from existing holders.

The root cause is exclusively in `ChainlinkPriceOracle.getAssetPrice()` at line 52, which is a necessary step in every L1 LST deposit. [4](#0-3)

### Citations

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

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```
