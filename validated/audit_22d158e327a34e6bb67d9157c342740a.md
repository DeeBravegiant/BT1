### Title
Missing L2 Sequencer Uptime Check in Chainlink Oracle Enables Stale-Price Exploitation on Arbitrum - (File: `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol`)

---

### Summary

`ChainlinkOracleForRSETHPoolCollateral.getRate()` calls Chainlink's `latestRoundData()` on L2 (Arbitrum) without verifying that the L2 sequencer is live. The existing staleness guards (`answeredInRound < roundID`, `timestamp == 0`) do not protect against sequencer downtime, because during a sequencer outage the round data itself freezes — no new round is opened, so `answeredInRound` never falls below `roundID`. Any user can deposit supported collateral tokens into the Arbitrum pool at the frozen (pre-outage) price and receive more rsETH than the tokens are actually worth.

---

### Finding Description

`ChainlinkOracleForRSETHPoolCollateral` is the oracle wrapper used for collateral tokens (e.g., wstETH) in the L2 pool contracts. Its `getRate()` function fetches the price via `latestRoundData()`:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol
function getRate() public view returns (uint256) {
    (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
        AggregatorV3Interface(oracle).latestRoundData();

    if (answeredInRound < roundID) revert StalePrice();   // ← does NOT catch sequencer downtime
    if (timestamp == 0) revert IncompleteRound();
    if (ethPrice <= 0) revert InvalidPrice();

    uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
    return normalizedPrice;
}
```

When the Arbitrum sequencer goes down, Chainlink stops updating its L2 price feeds. The last round remains open and `answeredInRound == roundID`, so the `StalePrice` guard never triggers. The price returned is the last value before the outage.

This oracle is consumed by every L2 pool contract through the `supportedTokenOracle` mapping. The call chain for a token deposit is:

1. `RSETHPool.deposit(token, amount, referralId)` (Arbitrum)
2. → `viewSwapRsETHAmountAndFee(amount, token)`
3. → `IOracle(supportedTokenOracle[token]).getRate()` — fetches stale collateral price
4. → `ChainlinkOracleForRSETHPoolCollateral.getRate()` — returns frozen price without sequencer check

The rsETH minted is calculated as:

```
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate
```

If `tokenToETHRate` is stale and inflated relative to the true market price, the user receives more rsETH than the deposited collateral is worth.

---

### Impact Explanation

**Impact: Medium — Theft of unclaimed yield / temporary over-issuance of rsETH**

During an Arbitrum sequencer outage, if a collateral token's price falls (e.g., wstETH drops from 1.2 ETH to 1.0 ETH), a user can deposit that token at the stale 1.2 ETH rate and receive rsETH priced at 1.2 ETH worth of value per token. The protocol has issued rsETH backed by collateral worth less than the rsETH issued. This directly dilutes the rsETH/ETH backing ratio and constitutes theft of value from existing rsETH holders and the protocol's liquidity pool.

---

### Likelihood Explanation

**Likelihood: Medium**

The Arbitrum sequencer has experienced documented outages. The protocol is explicitly deployed on Arbitrum (`RSETHPool.sol` is annotated "the pool contract for the rsETH pool on *Arbitrum*"). During any outage, the attack window is open for the full duration of the downtime. No special permissions are required — any unprivileged depositor can call `deposit(token, amount, referralId)`.

---

### Recommendation

Add a sequencer uptime check in `ChainlinkOracleForRSETHPoolCollateral.getRate()` following the Chainlink L2 Sequencer Uptime Feed pattern:

```solidity
AggregatorV3Interface public immutable sequencerUptimeFeed;
uint256 public constant GRACE_PERIOD = 3600; // 1 hour

function getRate() public view returns (uint256) {
    // Check sequencer uptime
    (, int256 sequencerAnswer, uint256 startedAt,,) = sequencerUptimeFeed.latestRoundData();
    if (sequencerAnswer == 1 || block.timestamp - startedAt < GRACE_PERIOD) {
        revert SequencerDown();
    }

    (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
        AggregatorV3Interface(oracle).latestRoundData();

    if (answeredInRound < roundID) revert StalePrice();
    if (timestamp == 0) revert IncompleteRound();
    if (ethPrice <= 0) revert InvalidPrice();

    return uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
}
```

The same fix should be applied to `ChainlinkPriceOracle.getAssetPrice()` if that contract is ever deployed on L2.

---

### Proof of Concept

1. Arbitrum sequencer goes offline. Chainlink L2 feeds freeze at last-known prices.
2. Off-chain, a collateral token (e.g., wstETH) drops 15% in value.
3. Attacker calls `RSETHPool.deposit(wstETH, 100e18, "")` on Arbitrum.
4. `viewSwapRsETHAmountAndFee` calls `ChainlinkOracleForRSETHPoolCollateral.getRate()`.
5. `latestRoundData()` returns the pre-outage price (15% higher than actual). `answeredInRound == roundID`, so `StalePrice` is not triggered.
6. Attacker receives rsETH calculated at the inflated wstETH price — more rsETH than the deposited wstETH is worth at current market value.
7. Sequencer comes back online. Attacker holds over-issued rsETH backed by under-valued collateral, extracting value from the pool. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** contracts/pools/RSETHPool.sol (L30-35)
```text
/// @title RSETHPool
/// @notice This contract is the pool contract for the rsETH pool on *Arbitrum*
/// @dev it differs from other RSETHPool contracts in other chains as it uses LZ_RSETH as the canonical rsETH token of
/// the chain.
/// @dev it was the first RSETHPool contract to be deployed in an L2 hence the legacy variables
contract RSETHPool is ERC20Upgradeable, AccessControlUpgradeable, ReentrancyGuardUpgradeable {
```

**File:** contracts/pools/RSETHPool.sol (L326-347)
```text
    function viewSwapRsETHAmountAndFee(
        uint256 amount,
        address token
    )
        public
        view
        onlySupportedToken(token)
        returns (uint256 rsETHAmount, uint256 fee)
    {
        uint256 feeBpsForToken = tokenFeeBps[token];
        fee = amount * feeBpsForToken / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
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
