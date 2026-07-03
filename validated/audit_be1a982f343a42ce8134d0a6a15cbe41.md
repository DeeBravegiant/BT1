### Title
Stale Chainlink Price Used Without Validation in `ChainlinkPriceOracle` Leads to Incorrect rsETH Minting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` consumes the raw Chainlink `latestRoundData()` answer with no staleness, incomplete-round, or invalid-price checks. This unadjusted price feeds directly into `LRTOracle._updateRsETHPrice()`, which sets the global `rsETHPrice` used by `LRTDepositPool.getRsETHAmountToMint()`. A stale price causes `rsETHPrice` to be set incorrectly, allowing depositors to receive more rsETH than they are entitled to, diluting existing holders and pushing the protocol toward insolvency.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` and discards every field except `price`:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L52
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

No check is made on `answeredInRound`, `updatedAt`, or whether `price > 0`. [1](#0-0) 

By contrast, the pool-level wrapper `ChainlinkOracleForRSETHPoolCollateral.getRate()` explicitly guards against all three failure modes:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

The unguarded `ChainlinkPriceOracle` is the oracle registered in `LRTOracle.assetPriceOracle` for mainnet LST assets (e.g., cbETH). `LRTOracle._getTotalEthInProtocol()` iterates every supported asset and calls `getAssetPrice(asset)` on it:

```solidity
uint256 assetER = getAssetPrice(asset);
uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
``` [3](#0-2) 

The result is used to compute and store `rsETHPrice`:

```solidity
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
...
rsETHPrice = newRsETHPrice;
``` [4](#0-3) 

`updateRSETHPrice()` is a **public, permissionless** function callable by anyone when the contract is not paused:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [5](#0-4) 

`LRTDepositPool.getRsETHAmountToMint()` then uses the stored `rsETHPrice` as the denominator when computing how many rsETH tokens to mint:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [6](#0-5) 

---

### Impact Explanation

**Scenario — stale price lower than actual (e.g., feed frozen before a price increase):**

1. A Chainlink feed for a supported LST (e.g., cbETH) becomes stale at price `P_stale < P_actual`.
2. Anyone calls `updateRSETHPrice()`. `_getTotalEthInProtocol()` uses `P_stale`, understating TVL.
3. `rsETHPrice` is set below its true value: `rsETHPrice_stale < rsETHPrice_true`.
4. A depositor calls `depositAsset(cbETH, amount, ...)`. The numerator uses the live `getAssetPrice(cbETH)` (still stale at `P_stale`), but the denominator is the already-committed `rsETHPrice_stale`.
5. If the feed updates between step 3 and step 4, the numerator rises while the denominator stays low, minting excess rsETH.
6. Alternatively, even with both stale, a subsequent `updateRSETHPrice()` after the feed recovers will reveal the protocol has issued more rsETH than the TVL supports — insolvency.

**Impact class**: Critical — protocol insolvency / direct dilution of existing rsETH holders' funds.

---

### Likelihood Explanation

Chainlink feeds can become stale during L1 network congestion, feed deprecation, or sequencer downtime on L2. The `updateRSETHPrice()` function is public and permissionless, so any actor (including an attacker monitoring feed staleness) can trigger the price commit at the worst moment. The inconsistency with `ChainlinkOracleForRSETHPoolCollateral` — which does perform these checks — confirms the omission is unintentional. Likelihood is **medium**: requires a feed to go stale, which is an observable on-chain condition.

---

### Recommendation

Apply the same three guards used in `ChainlinkOracleForRSETHPoolCollateral` inside `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Additionally, consider adding a `heartbeat`-based `updatedAt` check (e.g., `block.timestamp - updatedAt > MAX_DELAY`) per feed.

---

### Proof of Concept

1. Assume cbETH/ETH Chainlink feed is configured in `ChainlinkPriceOracle` and the feed's last update was at price `1.05e18` (stale; actual price is now `1.10e18`).
2. Attacker calls `LRTOracle.updateRSETHPrice()`. `_getTotalEthInProtocol()` reads `1.05e18` for cbETH. Suppose 1000 cbETH are in the protocol and rsETH supply is 1050. `rsETHPrice = (1000 * 1.05e18) / 1050 = 1.0e18`.
3. The Chainlink feed updates to `1.10e18`.
4. Attacker deposits 100 cbETH via `LRTDepositPool.depositAsset()`. `getRsETHAmountToMint` computes: `(100 * 1.10e18) / 1.0e18 = 110 rsETH`.
5. True fair value: `(100 * 1.10e18) / ((1000 * 1.10e18) / 1050) ≈ 100 rsETH`.
6. Attacker receives 110 rsETH instead of 100 — 10 rsETH of excess value extracted from existing holders, with no validation to prevent it.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L30-32)
```text
        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L250-313)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

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
        }

        // downside protection — pause if price drops too far
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

            // if price has decreased compared to the previous price, emit an event to reflect that
            if (previousPrice > newRsETHPrice) {
                emit RsETHPriceDecrease(newRsETHPrice, previousPrice);
            }

            // emit an event to notify that the price is currently below the peak (all time high) price
            emit RsETHPriceBelowPeak(highestRsethPrice, newRsETHPrice);
        }

        // update highest price if new price exceeds it
        if (newRsETHPrice > highestRsethPrice) {
            highestRsethPrice = newRsETHPrice;
        }

        // mint protocol fee as rsETH if there's a fee to take
        if (protocolFeeInETH > 0) {
            // Calculate rsETH amount to mint as protocol fee
            uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);

            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
            if (rsethAmountToMintAsProtocolFee > 0) {
                address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
                IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
                emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
            }
        } else {
            _checkAndUpdateDailyFeeMintLimit(0);
        }

        rsETHPrice = newRsETHPrice;
```

**File:** contracts/LRTOracle.sol (L339-343)
```text
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
