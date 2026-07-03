### Title
Stale Chainlink Price Accepted Without Staleness Check, Enabling Oracle Rate Abuse at Deposit - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all validity fields (`updatedAt`, `answeredInRound`), accepting arbitrarily stale prices. This price feeds directly into `LRTDepositPool.getRsETHAmountToMint()`, which determines how many rsETH tokens a depositor receives. An attacker who observes a stale (inflated) Chainlink price can deposit an LST and receive more rsETH than the true asset value warrants, diluting all existing rsETH holders.

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink round data but silently drops the staleness fields:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L52
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

The returned tuple is `(roundId, answer, startedAt, updatedAt, answeredInRound)`. Neither `updatedAt` (the timestamp of the last oracle update) nor `answeredInRound` (used to detect incomplete rounds) is checked. [1](#0-0) 

This price is consumed by `LRTOracle.getAssetPrice()`, which is called in two critical paths:

1. **`_getTotalEthInProtocol()`** — used inside `_updateRsETHPrice()` to compute the new `rsETHPrice` stored on-chain. [2](#0-1) 

2. **`getRsETHAmountToMint()`** — called at deposit time to determine how many rsETH tokens to mint per unit of deposited asset: [3](#0-2) 

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`rsETHPrice` is a stored value updated by a prior `updateRSETHPrice()` call, while `getAssetPrice(asset)` is read live at deposit time from the Chainlink feed. If the Chainlink feed is stale and reports a price higher than the true current price, the numerator is inflated while the denominator reflects an older (also potentially inflated) stored value — but the two values are computed at different times, creating an exploitable discrepancy.

### Impact Explanation

When a Chainlink feed is stale and reports an inflated LST/ETH price, a depositor receives more rsETH than the true ETH value of their deposit warrants. This over-minting dilutes the rsETH/ETH backing ratio for all existing holders, constituting a direct theft of value from current rsETH holders. The `pricePercentageLimit` guard in `_updateRsETHPrice()` only triggers on large price swings during oracle updates, not at deposit time. [4](#0-3) 

**Impact class:** High — theft of unclaimed yield / dilution of existing rsETH holders' backing.

### Likelihood Explanation

Chainlink feeds have heartbeat intervals (e.g., 24 hours for some LST/ETH feeds) and deviation thresholds (e.g., 0.5–1%). During periods of market stress, an LST price can drop by more than the deviation threshold before the feed updates. An attacker monitoring the mempool can observe the gap between the stale on-chain Chainlink price and the true market price and deposit during that window. No flash loan or special privilege is required — any unprivileged depositor can call `depositAsset()`. [5](#0-4) 

### Recommendation

Add staleness and round-completeness checks in `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

require(answeredInRound >= roundId, "Stale price: incomplete round");
require(updatedAt != 0, "Stale price: round not complete");
require(block.timestamp - updatedAt <= MAX_STALENESS, "Stale price: too old");
require(price > 0, "Invalid price");
```

`MAX_STALENESS` should be set per feed based on its documented heartbeat (e.g., 25 hours for a 24-hour heartbeat feed). [1](#0-0) 

### Proof of Concept

1. Observe that stETH/ETH Chainlink feed last updated 23 hours ago at price `1.05 ETH`. True market price has since dropped to `1.02 ETH` due to a depeg event, but the feed has not yet triggered a deviation update.
2. Call `LRTDepositPool.depositAsset(stETH, 100e18, minRSETH)` as an unprivileged attacker.
3. `getRsETHAmountToMint()` computes: `100e18 * 1.05e18 / rsETHPrice` — using the stale `1.05` price instead of the true `1.02`.
4. Attacker receives `~2.9%` more rsETH than the true ETH value of their deposit.
5. When the Chainlink feed eventually updates to `1.02`, `updateRSETHPrice()` is called and `rsETHPrice` drops, reducing the ETH backing per rsETH for all existing holders.
6. Attacker redeems their over-minted rsETH for more ETH than they deposited, at the expense of other holders. [1](#0-0) [3](#0-2)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
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

**File:** contracts/LRTOracle.sol (L331-349)
```text
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

            unchecked {
                ++assetIdx;
            }
        }
    }
```

**File:** contracts/LRTDepositPool.sol (L100-118)
```text
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
