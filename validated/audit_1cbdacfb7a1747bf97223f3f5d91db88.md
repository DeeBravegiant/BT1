### Title
Missing L2 Sequencer Uptime Check in Chainlink Oracle Allows Stale Price Exploitation - (`contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol`)

---

### Summary

`ChainlinkOracleForRSETHPoolCollateral.getRate()` calls Chainlink's `latestRoundData()` on L2 chains (Arbitrum, Optimism, etc.) without verifying that the L2 sequencer is live. When the sequencer is down, Chainlink feeds return the last pre-downtime price as if it were fresh, bypassing the existing staleness guards. This stale price is consumed directly by `RSETHPoolV3` to compute how much wrsETH a depositor receives, enabling an attacker to exploit the price discrepancy.

---

### Finding Description

`ChainlinkOracleForRSETHPoolCollateral.getRate()` fetches the collateral token price:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol
function getRate() public view returns (uint256) {
    (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
        AggregatorV3Interface(oracle).latestRoundData();

    if (answeredInRound < roundID) revert StalePrice();
    if (timestamp == 0) revert IncompleteRound();
    if (ethPrice <= 0) revert InvalidPrice();

    uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
    return normalizedPrice;
}
``` [1](#0-0) 

The existing checks (`answeredInRound < roundID`, `timestamp == 0`) only detect incomplete rounds. They do **not** detect the L2 sequencer-down scenario: when the sequencer is offline, Chainlink nodes cannot post new rounds to L2, so `latestRoundData()` returns the last pre-downtime round — which is a fully valid, completed round with a non-zero timestamp. Both guards pass, yet the price is arbitrarily stale.

This oracle is registered as the price source for supported collateral tokens in `RSETHPoolV3`:

```solidity
// contracts/pools/RSETHPoolV3.sol
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [2](#0-1) 

The same pattern exists in `ChainlinkPriceOracle.getAssetPrice()`, which is the core oracle for the mainnet deposit pool and also lacks any sequencer or staleness check:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [3](#0-2) 

---

### Impact Explanation

When the L2 sequencer is down and the real market price of a collateral token has dropped significantly below the stale Chainlink price, an attacker can deposit that collateral into `RSETHPoolV3` and receive wrsETH calculated at the inflated stale price. Once the sequencer recovers and prices normalize, the attacker holds more wrsETH than the deposited collateral is worth, extracting value from the pool's liquidity providers. This constitutes **theft of unclaimed yield / temporary fund theft** (Medium severity per the allowed impact scope).

---

### Likelihood Explanation

L2 sequencer outages are documented historical events on Arbitrum and Optimism. The `RSETHPoolV3` is deployed on multiple L2 chains (Arbitrum, Optimism, Base, Scroll, etc.) as evidenced by the cross-chain deployment addresses in the README. [4](#0-3)  Any sequencer downtime window during which the collateral token price moves materially creates an exploitable window. No special permissions are required — any depositor can call the swap/deposit functions.

---

### Recommendation

Add a sequencer uptime check before consuming the Chainlink price, following the [Chainlink L2 sequencer feed pattern](https://docs.chain.link/data-feeds/l2-sequencer-feeds#example-code):

```solidity
// Example addition to ChainlinkOracleForRSETHPoolCollateral
address public immutable sequencerUptimeFeed;
uint256 public constant GRACE_PERIOD = 3600; // 1 hour after sequencer restart

function getRate() public view returns (uint256) {
    // Check sequencer uptime
    (, int256 sequencerAnswer, uint256 startedAt,,) =
        AggregatorV3Interface(sequencerUptimeFeed).latestRoundData();
    bool isSequencerUp = sequencerAnswer == 0;
    if (!isSequencerUp) revert SequencerDown();
    if (block.timestamp - startedAt <= GRACE_PERIOD) revert GracePeriodNotOver();

    // Existing price fetch ...
    (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
        AggregatorV3Interface(oracle).latestRoundData();
    if (answeredInRound < roundID) revert StalePrice();
    if (timestamp == 0) revert IncompleteRound();
    if (ethPrice <= 0) revert InvalidPrice();
    return uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
}
```

Apply the same fix to `ChainlinkPriceOracle.getAssetPrice()` for any L2 deployments of the core protocol. [5](#0-4) 

---

### Proof of Concept

1. Arbitrum sequencer goes offline. ETH real market price drops from $3,000 → $2,000.
2. Chainlink's ETH/USD feed on Arbitrum is frozen at $3,000 (last pre-downtime round). `answeredInRound == roundID` and `timestamp != 0`, so both staleness guards in `getRate()` pass.
3. Attacker calls `RSETHPoolV3.swapTokenToRsETH(ETH, 1 ether)`. [6](#0-5) 
4. `tokenToETHRate = ChainlinkOracleForRSETHPoolCollateral.getRate()` returns the stale $3,000 price.
5. Attacker receives wrsETH priced at $3,000/ETH instead of the real $2,000/ETH — a 50% overpayment in wrsETH relative to deposited value.
6. Sequencer recovers; attacker redeems wrsETH at the correct price, extracting ~$1,000 per ETH deposited from the pool's liquidity. [1](#0-0)

### Citations

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

**File:** contracts/pools/RSETHPoolV3.sol (L301-308)
```text
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L331-334)
```text
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** README.md (L841-843)
```markdown
| Arbitrum     | 0xe119D214a6efa7d3cF60e6E59481EDe1B0064A6B     |
| Optimism     | 0x68A9EC5b93F04a60c77F486a664f283B2E4E2B72     |
| BSC          | 0x4186BFC76E2E237523CBC30FD220FE055156b41F     |
```
