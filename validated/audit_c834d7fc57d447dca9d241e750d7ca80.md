### Title
Missing Staleness Check on Chainlink `updatedAt` Timestamp Allows Stale Price Acceptance - (File: contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol)

### Summary
`ChainlinkOracleForRSETHPoolCollateral.getRate()` fetches the Chainlink `updatedAt` timestamp from `latestRoundData()` but never validates it against `block.timestamp`. The contract checks `answeredInRound < roundID` and `timestamp == 0`, but omits the critical heartbeat/staleness check (`block.timestamp - timestamp > maxStaleness`). This is the direct analog to the reported vulnerability class: a timestamp is obtained but never compared to the current time, allowing a stale price to be silently accepted and used for collateral valuation in the RSETHPool contracts.

### Finding Description
In `ChainlinkOracleForRSETHPoolCollateral.sol`, the `getRate()` function retrieves Chainlink price data:

```solidity
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

The `answeredInRound < roundID` guard only detects rounds that were never answered; it does **not** detect a round that was answered hours or days ago. The `timestamp` variable (Chainlink's `updatedAt`) is captured but the note `,,` in the destructuring shows it is silently discarded — there is no check of the form `block.timestamp - timestamp > STALE_THRESHOLD`. A Chainlink feed can stop updating (e.g., during L2 sequencer downtime, network congestion, or a feed deprecation) while still returning a valid `answeredInRound == roundID` and a non-zero `timestamp`, causing the stale price to pass all guards. [2](#0-1) 

### Impact Explanation
The oracle is used to price collateral assets (e.g., ETH/USD) in the RSETHPool family of contracts. If the feed goes stale while ETH's market price has fallen, the contract continues to report the last (higher) price. Any depositor can then supply ETH and receive rsETH/wrsETH minted at the inflated stale rate, extracting more value than the current market warrants. This constitutes **theft of yield** from existing rsETH holders (their share of the backing pool is diluted) and, at sufficient scale, **protocol insolvency**. Impact: **High** (theft of unclaimed yield) to **Critical** (protocol insolvency).

### Likelihood Explanation
Chainlink feeds on L2 networks (Arbitrum, Optimism, Base, etc.) — where the RSETHPool contracts are deployed — are subject to sequencer downtime. During sequencer outages, the feed cannot update. A sophisticated depositor monitoring the mempool can observe the staleness window and submit a deposit transaction the moment the sequencer resumes, before the oracle updates, using the stale (higher) price. This is a realistic, externally-triggered but contract-exploitable scenario requiring no privileged access.

### Recommendation
Add a configurable maximum staleness threshold and validate `updatedAt` against `block.timestamp`:

```solidity
uint256 public constant MAX_STALENESS = 3600; // e.g., 1 hour

function getRate() public view returns (uint256) {
    (uint80 roundID, int256 ethPrice,, uint256 updatedAt, uint80 answeredInRound) =
        AggregatorV3Interface(oracle).latestRoundData();

    if (answeredInRound < roundID) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (block.timestamp - updatedAt > MAX_STALENESS) revert StalePrice(); // ← add this
    if (ethPrice <= 0) revert InvalidPrice();

    return uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
}
```

### Proof of Concept
1. Chainlink ETH/USD feed on an L2 last reported `ethPrice = 4000e8` at `updatedAt = T`.
2. L2 sequencer goes offline for 2 hours; ETH market price drops to 3000 USD.
3. Sequencer resumes. The Chainlink feed has not yet posted a new round; `answeredInRound == roundID` and `updatedAt == T` (2 hours ago).
4. Attacker calls `deposit(ETH, amount)` on the RSETHPool. `ChainlinkOracleForRSETHPoolCollateral.getRate()` returns `4000e18` — all three guards pass.
5. Attacker receives rsETH minted at the 4000 USD/ETH rate instead of the correct 3000 USD/ETH rate — a ~33% overmint at the expense of existing rsETH holders.
6. Attacker immediately redeems or sells rsETH, extracting the difference. [3](#0-2)

### Citations

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L26-42)
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
}
```
