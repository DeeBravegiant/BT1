### Title
Missing Price > 0 Validation in Chainlink Oracle Allows Zero-Price Propagation into Core Accounting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` fetches `int256 price` from Chainlink's `latestRoundData()` but never validates that `price > 0` before casting and returning it. A zero price silently propagates into deposit minting, TVL computation, and withdrawal calculations, enabling depositor fund loss and protocol-wide temporary freeze.

### Finding Description
In `ChainlinkPriceOracle.getAssetPrice()`, the raw Chainlink answer is used without a positivity check:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L49-L55
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Chainlink can return `price == 0` during circuit-breaker events or feed initialization. When `price == 0`, `uint256(0)` is returned silently with no revert.

This zero propagates into three critical paths:

**Path 1 — Deposit minting (`LRTDepositPool.getRsETHAmountToMint`):** [1](#0-0) 
`rsethAmountToMint = (amount * 0) / rsETHPrice = 0`. If the caller passes `minRSETHAmountExpected = 0`, the deposit proceeds, assets are transferred in, and the user receives 0 rsETH — a direct loss of deposited funds.

**Path 2 — TVL / rsETH price computation (`LRTOracle._getTotalEthInProtocol`):** [2](#0-1) 
A zero `assetER` for any supported asset zeroes out that asset's entire TVL contribution. The resulting artificially low `totalETHInProtocol` causes `newRsETHPrice` to fall below `highestRsethPrice`, potentially crossing the `pricePercentageLimit` threshold and triggering the automatic protocol pause (deposit pool + withdrawal manager + oracle all paused). [3](#0-2) 

**Path 3 — Withdrawal amount calculation (`LRTWithdrawalManager.getExpectedAssetAmount`):** [4](#0-3) 
Division by zero reverts, freezing all withdrawal requests for the affected asset.

The same codebase's `ChainlinkOracleForRSETHPoolCollateral` already applies the correct guard (`if (ethPrice <= 0) revert InvalidPrice()`), confirming the developers are aware of this risk but omitted it from the primary oracle: [5](#0-4) 

### Impact Explanation
- **Critical**: A depositor calling `depositAsset()` or `depositETH()` with `minRSETHAmountExpected = 0` during a zero-price window loses their entire deposited amount — assets are transferred in, 0 rsETH is minted.
- **Medium**: A zero price for any supported asset causes `_updateRsETHPrice()` to compute an artificially low rsETH price, potentially triggering the automatic downside-protection pause, temporarily freezing all deposits and withdrawals protocol-wide.

### Likelihood Explanation
Chainlink feeds can return 0 during circuit-breaker activations, feed deprecation, or during the brief window after a new aggregator is deployed. The `updatePriceFeedFor()` function in `ChainlinkPriceOracle` requires only `LRTManager` role and a non-zero address — no price sanity check is performed at registration time. Any public depositor or withdrawer is affected the moment a zero price is live. [6](#0-5) 

### Recommendation
Add a positivity check in `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();
    if (price <= 0) revert InvalidPrice();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Additionally, consider adding staleness checks (`updatedAt`, `answeredInRound < roundId`) consistent with `ChainlinkOracleForRSETHPoolCollateral`.

### Proof of Concept
1. Chainlink feed for a supported LST (e.g., stETH) enters a circuit-breaker state and `latestRoundData()` returns `price = 0`.
2. Any user calls `LRTDepositPool.depositAsset(stETH, 1e18, 0, "")`.
3. `_beforeDeposit` calls `getRsETHAmountToMint(stETH, 1e18)`.
4. `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns `0`.
5. `rsethAmountToMint = (1e18 * 0) / rsETHPrice = 0`.
6. `0 >= minRSETHAmountExpected (0)` passes the slippage check.
7. `IERC20(stETH).safeTransferFrom(user, depositPool, 1e18)` executes — user's 1 stETH is taken.
8. `_mintRsETH(0)` mints nothing — user receives 0 rsETH.
9. Simultaneously, `updateRSETHPrice()` called by any public caller computes `totalETHInProtocol` with stETH's contribution = 0, causing `newRsETHPrice` to drop sharply, potentially triggering the automatic pause of the entire protocol.

### Citations

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
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

**File:** contracts/LRTOracle.sol (L336-344)
```text
        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

```

**File:** contracts/LRTWithdrawalManager.sol (L590-594)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L30-33)
```text
        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

```

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L61-65)
```text
    function updatePriceFeedFor(address asset, address priceFeed) external onlyLRTManager onlySupportedAsset(asset) {
        UtilLib.checkNonZeroAddress(priceFeed);
        assetPriceFeed[asset] = priceFeed;
        emit AssetPriceFeedUpdate(asset, priceFeed);
    }
```
