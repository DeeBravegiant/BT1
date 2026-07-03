### Title
Missing Arbitrum Sequencer Uptime Check in Chainlink Oracle Allows Stale Price Usage - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls Chainlink's `latestRoundData()` with no check for whether the Arbitrum L2 sequencer is active. The protocol is deployed on Arbitrum (confirmed in README). When the sequencer is offline, Chainlink price feeds on L2 return stale cached prices. This allows depositors to mint rsETH using stale LST prices, enabling over-minting if an LST depegs while the sequencer is down.

### Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches the price of LST assets (stETH, rETH, etc.) via Chainlink:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

There is no sequencer uptime feed check, no staleness check (`updatedAt` is ignored), and no `answeredInRound >= roundId` validation. [1](#0-0) 

The companion pool oracle `ChainlinkOracleForRSETHPoolCollateral` does implement `answeredInRound < roundID` and `timestamp == 0` guards, but still omits the sequencer uptime check. [2](#0-1) 

A grep across all production contracts confirms zero sequencer uptime feed references anywhere in the codebase. 

`ChainlinkPriceOracle` feeds into `LRTOracle` which computes the rsETH price used by deposit pools. `RSETHPoolV2.deposit()` calls `viewSwapRsETHAmountAndFee()` → `getRate()` → the oracle chain, meaning every deposit on Arbitrum is priced through this stale-price-vulnerable path. [3](#0-2) 

### Impact Explanation
If an LST (e.g., stETH) depegs while the Arbitrum sequencer is offline, the stale pre-depeg price remains in the Chainlink feed. A depositor can supply the depegged LST at the stale (inflated) price and receive rsETH minted at that inflated rate. When the sequencer comes back online and the oracle updates, the rsETH backing is worth less than the rsETH minted — protocol insolvency / theft of funds from existing rsETH holders.

**Impact: Critical** — direct theft of funds from existing rsETH holders via over-minting against stale collateral prices.

### Likelihood Explanation
Arbitrum sequencer outages have occurred historically (e.g., December 2023). The protocol is explicitly deployed on Arbitrum. [4](#0-3)  Any sequencer downtime coinciding with LST price volatility creates the exploit window. No special permissions are required — any depositor can trigger this.

### Recommendation
Integrate Chainlink's L2 Sequencer Uptime Feed in `ChainlinkPriceOracle.getAssetPrice()` (and `ChainlinkOracleForRSETHPoolCollateral.getRate()`). Before consuming any price, check:

```solidity
(, int256 answer, uint256 startedAt,,) = sequencerUptimeFeed.latestRoundData();
if (answer != 0) revert SequencerDown();
if (block.timestamp - startedAt < GRACE_PERIOD) revert GracePeriodNotOver();
```

Also add a staleness check on `updatedAt` in `ChainlinkPriceOracle.getAssetPrice()` (currently entirely absent). [1](#0-0) 

### Proof of Concept
1. Arbitrum sequencer goes offline.
2. An LST (e.g., stETH) depegs to 0.90 ETH on mainnet; Chainlink L2 feed is frozen at 1.00 ETH.
3. Attacker acquires depegged stETH cheaply on a CEX or via mainnet.
4. Attacker calls `RSETHPoolV2.deposit()` (or the equivalent V3 pool) on Arbitrum with the depegged stETH.
5. `viewSwapRsETHAmountAndFee()` calls `getRate()` → `ChainlinkPriceOracle.getAssetPrice()` → returns stale 1.00 ETH price. [5](#0-4) 
6. rsETH is minted at the stale 1:1 rate instead of the correct 0.90:1 rate — attacker receives ~11% excess rsETH.
7. Sequencer comes back online; oracle updates to 0.90 ETH; rsETH is now undercollateralized, harming all existing holders.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
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

**File:** contracts/pools/RSETHPoolV2.sol (L225-234)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```

**File:** README.md (L33-33)
```markdown
  - [Arbitrum](#arbitrum)
```
