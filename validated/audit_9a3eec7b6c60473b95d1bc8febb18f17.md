### Title
Missing L2 Sequencer Uptime Check When Fetching Chainlink Prices in RSETHPool on Arbitrum - (File: contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol)

### Summary
`ChainlinkOracleForRSETHPoolCollateral.getRate()` calls Chainlink's `latestRoundData()` without checking the Arbitrum sequencer's uptime. Since `RSETHPool` is explicitly deployed on Arbitrum, a sequencer outage allows stale collateral prices to be consumed by the pool's deposit logic, enabling an attacker to receive more rsETH LP tokens than the deposited collateral is worth.

### Finding Description
`ChainlinkOracleForRSETHPoolCollateral` is the Chainlink oracle wrapper used to price collateral tokens (e.g., wstETH/ETH) inside the Arbitrum RSETHPool. Its `getRate()` function calls `AggregatorV3Interface(oracle).latestRoundData()` and applies three guards:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [1](#0-0) 

None of these guards protect against sequencer downtime. When the Arbitrum sequencer is offline, Chainlink feeds stop updating but the round counter also stops advancing, so `answeredInRound == roundID` still holds — the `StalePrice` revert is never triggered. The feed silently returns the last pre-downtime price.

`RSETHPool` is explicitly the Arbitrum L2 pool: [2](#0-1) 

It stores a `supportedTokenOracle` mapping and calls `IOracle(oracle).getRate()` to value collateral during deposits: [3](#0-2) 

`ChainlinkOracleForRSETHPoolCollateral` is the concrete implementation of that oracle interface, confirmed by the `getRate()` / `rate()` entry points it exposes: [4](#0-3) 

### Impact Explanation
**Medium — Temporary freezing of funds / theft of unclaimed yield.**

During an Arbitrum sequencer outage, if the collateral token's real market price has fallen below the last Chainlink-reported price, an attacker can deposit collateral at the inflated stale rate and receive more rsETH LP tokens than the collateral is worth. When the sequencer resumes and prices normalise, the attacker redeems LP tokens for a profit at the expense of honest LPs. Conversely, if the stale price is lower than reality, honest depositors are under-credited, constituting a temporary loss of yield. In either direction the pool's accounting is corrupted for the duration of the outage.

### Likelihood Explanation
Arbitrum sequencer outages have occurred historically (e.g., December 2022, June 2023). The attack window is bounded by the outage duration, but no special permissions are required — any public depositor can trigger it. The attacker only needs to monitor sequencer status and submit a deposit transaction via the L1 delayed inbox (which bypasses the sequencer) while the outage is active.

### Recommendation
Follow the [Chainlink L2 Sequencer Uptime Feeds](https://docs.chain.link/data-feeds/l2-sequencer-feeds) pattern. Add a sequencer uptime feed check at the top of `getRate()`:

```solidity
// sequencerUptimeFeed = Chainlink Arbitrum sequencer uptime feed
(, int256 answer, uint256 startedAt,,) = sequencerUptimeFeed.latestRoundData();
if (answer != 0) revert SequencerDown();
if (block.timestamp - startedAt < GRACE_PERIOD) revert GracePeriodNotOver();
```

This should be added to `ChainlinkOracleForRSETHPoolCollateral.getRate()` before the existing staleness checks. [5](#0-4) 

### Proof of Concept
1. Arbitrum sequencer goes offline. The wstETH/ETH Chainlink feed last reported `1.15 ETH` per wstETH; the real market price has since dropped to `1.05 ETH`.
2. Attacker submits a deposit of 100 wstETH to `RSETHPool` via the L1 delayed inbox (bypassing the sequencer).
3. `RSETHPool` calls `IOracle(supportedTokenOracle[wstETH]).getRate()` → `ChainlinkOracleForRSETHPoolCollateral.getRate()` → `latestRoundData()` returns the stale `1.15 ETH` price. All three guards pass (`answeredInRound == roundID`, `timestamp != 0`, `price > 0`).
4. The pool mints LP tokens valued at `115 ETH` worth of rsETH for a deposit actually worth `105 ETH`.
5. Sequencer resumes; attacker redeems LP tokens, extracting `~10 ETH` of value from the pool at the expense of other LPs. [6](#0-5) [2](#0-1)

### Citations

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L26-41)
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

    function rate() external view returns (uint256) {
        return getRate();
    }
```

**File:** contracts/pools/RSETHPool.sol (L31-34)
```text
/// @notice This contract is the pool contract for the rsETH pool on *Arbitrum*
/// @dev it differs from other RSETHPool contracts in other chains as it uses LZ_RSETH as the canonical rsETH token of
/// the chain.
/// @dev it was the first RSETHPool contract to be deployed in an L2 hence the legacy variables
```

**File:** contracts/pools/RSETHPool.sol (L59-59)
```text
    mapping(address token => address oracle) public supportedTokenOracle;
```
