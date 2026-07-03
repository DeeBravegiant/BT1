Audit Report

## Title
Missing `price > 0` Validation in `ChainlinkPriceOracle.getAssetPrice()` Allows Zero-Price Propagation into Core Accounting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` fetches `int256 price` from `latestRoundData()` and casts it directly to `uint256` without validating `price > 0`. A zero return from Chainlink silently propagates into deposit minting, TVL computation, and withdrawal calculations. The same codebase's `ChainlinkOracleForRSETHPoolCollateral` already applies the correct `if (ethPrice <= 0) revert InvalidPrice()` guard, confirming developer awareness of the risk.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` performs no positivity check:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L49-L55
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

When `price == 0`, `uint256(0)` is returned with no revert. This zero propagates into three paths:

**Path 1 — Deposit fund loss (`LRTDepositPool.depositAsset`):**
`getRsETHAmountToMint` computes `rsethAmountToMint = (amount * 0) / rsETHPrice = 0`. The slippage guard at `_beforeDeposit` only reverts if `rsethAmountToMint < minRSETHAmountExpected`; when `minRSETHAmountExpected == 0` (a common default in automated callers), `0 < 0` is false and execution continues. `safeTransferFrom` takes the user's assets, then `_mintRsETH(0)` mints nothing — deposited funds are lost.

**Path 2 — Protocol-wide temporary freeze (`LRTOracle._updateRsETHPrice`):**
`_getTotalEthInProtocol()` calls `getAssetPrice(asset)` for every supported asset. A zero price for any asset zeroes out that asset's entire TVL contribution, causing `newRsETHPrice` to drop sharply. If `pricePercentageLimit > 0` and the drop exceeds the threshold, `_updateRsETHPrice` automatically pauses `lrtDepositPool`, `withdrawalManager`, and the oracle itself. `updateRSETHPrice()` is `public` — any unprivileged caller can trigger this.

**Path 3 — Withdrawal revert (`LRTWithdrawalManager.getExpectedAssetAmount`):**
`underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset)` — division by zero causes a Solidity 0.8 panic revert, freezing `initiateWithdrawal` and `instantWithdrawal` for the affected asset.

Existing guards are insufficient: `updatePriceFeedFor` only checks for a non-zero address, not a valid price. `updatePriceOracleForValidated` in `LRTOracle` checks `price > 1e16` at registration time but does not protect against a feed that later returns 0 at runtime.

## Impact Explanation
- **Critical — Direct theft of user funds**: A depositor calling `depositAsset(asset, amount, 0, "")` with `minRSETHAmountExpected = 0` during a zero-price window loses their entire deposited amount. Assets are transferred in via `safeTransferFrom`; 0 rsETH is minted. This is a direct, irreversible loss of deposited principal.
- **Medium — Temporary freezing of funds**: Any public caller invoking `updateRSETHPrice()` while a zero price is live triggers the automatic downside-protection pause, freezing all deposits and withdrawals protocol-wide until an admin manually unpauses. Simultaneously, `initiateWithdrawal` and `instantWithdrawal` revert with division-by-zero for the affected asset.

## Likelihood Explanation
Chainlink feeds are documented to return `answer == 0` during feed deprecation, aggregator replacement windows, or circuit-breaker activation. The `updatePriceFeedFor` function requires only `LRTManager` role and a non-zero address — no live price sanity check is enforced at registration. Once a zero price is live, any public depositor or caller of `updateRSETHPrice()` can trigger the impact without any privileged access. The `minRSETHAmountExpected = 0` condition is common in automated scripts, aggregators, and users unfamiliar with the slippage parameter.

## Recommendation
Add a positivity check in `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();
    if (price <= 0) revert InvalidPrice();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Additionally, add staleness checks (`answeredInRound < roundId`, `updatedAt == 0`) consistent with `ChainlinkOracleForRSETHPoolCollateral`. Consider also enforcing `minRSETHAmountExpected > 0` in `_beforeDeposit` as a defense-in-depth measure.

## Proof of Concept
**Fund loss path:**
1. Chainlink feed for a supported LST (e.g., stETH) returns `price = 0` from `latestRoundData()`.
2. User (or bot) calls `LRTDepositPool.depositAsset(stETH, 1e18, 0, "")`.
3. `_beforeDeposit` → `getRsETHAmountToMint(stETH, 1e18)` → `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns `0`.
4. `rsethAmountToMint = (1e18 * 0) / rsETHPrice = 0`.
5. `0 < 0` (slippage check) → false → no revert.
6. `IERC20(stETH).safeTransferFrom(user, depositPool, 1e18)` executes — 1 stETH taken.
7. `_mintRsETH(0)` → user receives 0 rsETH.

**Temporary freeze path:**
1. Same zero-price condition.
2. Any address calls `LRTOracle.updateRSETHPrice()` (public, no role required).
3. `_getTotalEthInProtocol()` computes stETH TVL contribution as 0.
4. `newRsETHPrice` drops sharply below `highestRsethPrice`.
5. If `diff > pricePercentageLimit.mulWad(highestRsethPrice)`, `lrtDepositPool.pause()`, `withdrawalManager.pause()`, and `_pause()` are called — entire protocol frozen.

**Foundry fork test outline:**
```solidity
function test_zeroPriceFundLoss() public {
    // Mock Chainlink feed to return price = 0
    vm.mockCall(priceFeed, abi.encodeWithSelector(AggregatorV3Interface.latestRoundData.selector),
        abi.encode(1, int256(0), block.timestamp, block.timestamp, 1));
    uint256 balanceBefore = stETH.balanceOf(user);
    vm.prank(user);
    depositPool.depositAsset(address(stETH), 1e18, 0, "");
    assertEq(rsETH.balanceOf(user), 0); // user received nothing
    assertLt(stETH.balanceOf(user), balanceBefore); // user lost funds
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8) [10](#0-9)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L61-65)
```text
    function updatePriceFeedFor(address asset, address priceFeed) external onlyLRTManager onlySupportedAsset(asset) {
        UtilLib.checkNonZeroAddress(priceFeed);
        assetPriceFeed[asset] = priceFeed;
        emit AssetPriceFeedUpdate(asset, priceFeed);
    }
```

**File:** contracts/LRTDepositPool.sol (L111-115)
```text
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
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
