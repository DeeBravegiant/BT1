### Title
Missing Arbitrum Sequencer Uptime Check in `ChainlinkOracleForRSETHPoolCollateral` Used by L2 Pool Contracts - (File: contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol)

---

### Summary
`ChainlinkOracleForRSETHPoolCollateral.getRate()` calls Chainlink's `latestRoundData()` without verifying that the Arbitrum (or other L2) sequencer is live. This oracle is consumed directly by `RSETHPool` (Arbitrum) and `RSETHPoolNoWrapper` (Arbitrum / Unichain) to price collateral tokens during user deposits. If the sequencer goes offline and then resumes, the feed may return a stale price, allowing depositors to receive an incorrect amount of rsETH.

---

### Finding Description

`ChainlinkOracleForRSETHPoolCollateral.getRate()` fetches the collateral price via `latestRoundData()`: [1](#0-0) 

The function performs three sanity checks — `answeredInRound < roundID`, `timestamp == 0`, and `ethPrice <= 0` — but **none of these detect a sequencer outage**. When an Arbitrum sequencer goes offline, Chainlink's L2 price feeds stop updating; the `updatedAt` timestamp freezes and `answeredInRound` remains equal to `roundID`, so all three existing guards pass silently while the returned price is stale.

This oracle is set as the collateral price source in `RSETHPool` (explicitly the Arbitrum pool): [2](#0-1) 

and in `RSETHPoolNoWrapper`, which covers Arbitrum and Unichain: [3](#0-2) 

Both contracts store per-token oracle addresses in `supportedTokenOracle` and call `IOracle(oracle).getRate()` to value deposited collateral: [4](#0-3) 

The `ChainlinkOracleForRSETHPoolCollateral` contract is the concrete implementation of `IOracle` used for these collateral tokens (e.g., wstETH/ETH), as confirmed by the oracle directory: [5](#0-4) 

---

### Impact Explanation

When the Arbitrum sequencer is offline or has just restarted after a downtime period, the Chainlink feed returns the last price recorded before the outage. Depending on the direction of price movement during the outage:

- **Over-minting**: If the stale price is higher than the true current price (e.g., ETH was $3 000 before the outage, now $2 000), a depositor receives more rsETH than the collateral is worth, extracting value from the pool at the expense of existing rsETH holders.
- **Under-minting / effective freeze**: If the stale price is lower than the true price, legitimate depositors receive fewer rsETH tokens than they are entitled to, temporarily freezing the fair value of their deposit.

**Impact: Medium — Temporary freezing of funds / theft of unclaimed yield.**

---

### Likelihood Explanation

Arbitrum sequencer outages have occurred historically (e.g., December 2021, June 2023). The window of exploitability opens immediately after the sequencer resumes, before the price feed catches up. Any unprivileged depositor can call the deposit function during this window with no special permissions required.

---

### Recommendation

Follow the Chainlink L2 Sequencer Uptime Feed pattern documented at https://docs.chain.link/data-feeds/l2-sequencer-feeds. Before consuming `latestRoundData()`, query the sequencer uptime feed and revert if the sequencer is down or if the grace period after restart has not elapsed:

```solidity
// Example addition to ChainlinkOracleForRSETHPoolCollateral.getRate()
AggregatorV3Interface sequencerFeed = AggregatorV3Interface(SEQUENCER_UPTIME_FEED);
(, int256 answer, uint256 startedAt,,) = sequencerFeed.latestRoundData();
if (answer != 0) revert SequencerDown();
if (block.timestamp - startedAt < GRACE_PERIOD) revert GracePeriodNotOver();
```

Apply the same guard to `ChainlinkPriceOracle.getAssetPrice()` if that contract is ever deployed on an L2. [1](#0-0) 

---

### Proof of Concept

1. Arbitrum sequencer goes offline. ETH price at outage: $3 000. Chainlink feed freezes at $3 000.
2. Sequencer resumes. True ETH price is now $2 000. Chainlink feed has not yet updated.
3. Attacker calls the deposit function on `RSETHPool` (Arbitrum) with 1 ETH of wstETH collateral.
4. `RSETHPool` calls `IOracle(supportedTokenOracle[wstETH]).getRate()` → `ChainlinkOracleForRSETHPoolCollateral.getRate()` → `latestRoundData()` returns stale $3 000 price.
5. All three guards (`answeredInRound < roundID`, `timestamp == 0`, `ethPrice <= 0`) pass because the round data is internally consistent — it is simply old.
6. Attacker receives rsETH valued at $3 000/ETH while depositing collateral worth $2 000/ETH, extracting ~$1 000 of value per ETH from the pool. [6](#0-5)

### Citations

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L13-37)
```text
/// @title ChainlinkOracleForRSETHPoolCollateral Contract
/// @notice Wrapper contract for Chainlink oracles
contract ChainlinkOracleForRSETHPoolCollateral {
    address public immutable oracle;

    error StalePrice();
    error IncompleteRound();
    error InvalidPrice();

    constructor(address _oracle) {
        oracle = _oracle;
    }

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

**File:** contracts/pools/RSETHPool.sol (L31-33)
```text
/// @notice This contract is the pool contract for the rsETH pool on *Arbitrum*
/// @dev it differs from other RSETHPool contracts in other chains as it uses LZ_RSETH as the canonical rsETH token of
/// the chain.
```

**File:** contracts/pools/RSETHPool.sol (L59-60)
```text
    mapping(address token => address oracle) public supportedTokenOracle;
    address[] public supportedTokenList;
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L27-30)
```text
/// @title RSETHPoolNoWrapper
/// @notice This contract is the deposit pool for the chains where there is no rsETH wrapper contract (e.g. Arbitrum,
/// Unichain)
contract RSETHPoolNoWrapper is AccessControlUpgradeable, PausableUpgradeable, ReentrancyGuardUpgradeable {
```
