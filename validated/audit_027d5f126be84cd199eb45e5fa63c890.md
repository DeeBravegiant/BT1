### Title
Chainlink minAnswer Circuit Breaker Not Validated in `ChainlinkPriceOracle`, Enabling Excess rsETH Minting During Asset Crash - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but never validates the returned price against Chainlink's built-in `minAnswer`/`maxAnswer` circuit breaker bounds. If a supported LST asset crashes below its `minAnswer`, Chainlink will persistently report the floor price rather than the real price. Any depositor can then call `LRTDepositPool.depositAsset()` with the crashed asset and receive rsETH minted at the inflated floor price, directly stealing value from existing rsETH holders.

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price and returns it without any bounds check:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

This price flows directly into `LRTDepositPool.getRsETHAmountToMint()`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [2](#0-1) 

`rsETHPrice` is a stored value updated separately by `updateRSETHPrice()`. If an LST asset crashes below its Chainlink `minAnswer` (e.g., stETH crashes to 0.10 ETH but `minAnswer` is 0.95 ETH), `getAssetPrice(stETH)` returns 0.95 ETH while the real value is 0.10 ETH. A depositor calling `depositAsset(stETH, amount, ...)` receives `amount * 0.95 / rsETHPrice` rsETH instead of `amount * 0.10 / rsETHPrice` — a 9.5x excess. [3](#0-2) 

The same unchecked pattern exists in `ChainlinkOracleForRSETHPoolCollateral.getRate()`, which is used as the collateral token oracle in pool contracts (`RSETHPoolV3`, `RSETHPoolV3WithNativeChainBridge`, etc.):

```solidity
(uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
    AggregatorV3Interface(oracle).latestRoundData();
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [4](#0-3) 

It checks for staleness and zero price but has no `minAnswer`/`maxAnswer` guard. An inflated `tokenToETHRate` from this oracle causes `viewSwapRsETHAmountAndFee(amount, token)` to compute excess rsETH for a crashed collateral token:

```solidity
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [5](#0-4) 

The `pricePercentageLimit` guard in `LRTOracle._updateRsETHPrice()` does not protect against this: it only gates the rsETH price update, not the per-asset price used at deposit time. [6](#0-5) 

### Impact Explanation

**Critical — Direct theft of user funds.**

When a supported LST crashes below its Chainlink `minAnswer`, any depositor can mint rsETH at the inflated floor price. The excess rsETH represents a claim on protocol TVL that was not backed by real value. All existing rsETH holders are diluted: when they withdraw, they receive less ETH per rsETH than they deposited. The attacker can immediately redeem the excess rsETH via `LRTWithdrawalManager` for other healthy assets, extracting real value from the pool. [7](#0-6) 

### Likelihood Explanation

**Medium.** This requires a supported LST to crash significantly — a real-world scenario (LUNA crash, stETH depeg events). The protocol holds stETH, ETHx, rETH, swETH, and sfrxETH as supported assets, all of which have Chainlink feeds with configured `minAnswer` floors. No privileged access is required; any depositor can exploit this the moment the circuit breaker activates.

### Recommendation

In `ChainlinkPriceOracle.getAssetPrice()`, retrieve the aggregator's `minAnswer` and `maxAnswer` and revert if the returned price is outside those bounds:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
int192 minAnswer = IOffchainAggregator(priceFeed.aggregator()).minAnswer();
int192 maxAnswer = IOffchainAggregator(priceFeed.aggregator()).maxAnswer();
if (price <= minAnswer || price >= maxAnswer) revert InvalidPrice();
```

Apply the same guard in `ChainlinkOracleForRSETHPoolCollateral.getRate()`.

### Proof of Concept

1. Assume stETH is a supported asset with a Chainlink feed whose `minAnswer` = 0.95e18 (ETH-denominated).
2. stETH depegs to 0.10 ETH. Chainlink circuit breaker activates; `latestRoundData()` returns `answer = 0.95e18`.
3. `rsETHPrice` was last updated at 1.05e18 (normal conditions).
4. Attacker calls `LRTDepositPool.depositAsset(stETH, 1e18, 0, "")`.
5. `getRsETHAmountToMint` computes: `1e18 * 0.95e18 / 1.05e18 ≈ 0.904e18` rsETH minted.
6. Actual fair value: `1e18 * 0.10e18 / 1.05e18 ≈ 0.095e18` rsETH.
7. Attacker receives ~9.5x excess rsETH, redeemable for healthy assets via `LRTWithdrawalManager.initiateWithdrawal()`. [8](#0-7) [9](#0-8) [10](#0-9)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
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

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L370-370)
```text
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/LRTOracle.sol (L252-266)
```text
        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
```

**File:** contracts/LRTWithdrawalManager.sol (L590-593)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```
