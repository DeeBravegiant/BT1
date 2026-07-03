### Title
`RSETHPriceFeed.getRoundData()` Applies Current rsETH/ETH Rate to Historical ETH/USD Round Data, Producing Incorrect rsETH/USD Price - (File: contracts/oracles/RSETHPriceFeed.sol)

---

### Summary

`RSETHPriceFeed.getRoundData(_roundId)` fetches historical ETH/USD price data for a specific Chainlink round but unconditionally multiplies it by the **current** `rsETHPrice()` from `LRTOracle`. This is a direct structural analog to the Hyperdrive checkpoint bug: a historical data point is retroactively stamped with a price that did not exist at that point in time, producing a fabricated and incorrect rsETH/USD answer for any non-latest round.

---

### Finding Description

`RSETHPriceFeed` is a Chainlink `AggregatorV3Interface`-compatible contract that exposes rsETH/USD pricing by composing the ETH/USD Chainlink feed with the on-chain rsETH/ETH rate.

The `latestRoundData()` implementation is correct: it fetches the latest ETH/USD round and multiplies by the current rsETH/ETH rate, both of which are contemporaneous.

The `getRoundData(_roundId)` implementation is broken:

```solidity
// contracts/oracles/RSETHPriceFeed.sol  lines 53-61
function getRoundData(uint80 _roundId)
    external
    view
    returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
{
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.getRoundData(_roundId);
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
}
``` [1](#0-0) 

- `ETH_TO_USD.getRoundData(_roundId)` returns the ETH/USD price **at the time of that historical round** — a lower value for older rounds.
- `RS_ETH_ORACLE.rsETHPrice()` always returns the **current** rsETH/ETH exchange rate stored in `LRTOracle`, which monotonically increases over time as staking rewards accrue.
- The product is therefore a synthetic rsETH/USD price that mixes a past ETH/USD value with a present rsETH/ETH value — a combination that never existed on-chain.

The `updatedAt` and `answeredInRound` fields returned are those of the historical ETH/USD round, so the timestamp metadata signals an old price while the rsETH component is current. This is the exact same mis-accounting pattern as Hyperdrive: a historical slot is retroactively assigned a price from a later point in time. [2](#0-1) 

---

### Impact Explanation

**Low — Contract fails to deliver promised returns, but doesn't lose value.**

Any external protocol (lending market, derivatives platform, aggregator) that integrates `RSETHPriceFeed` as a Chainlink-compatible oracle and calls `getRoundData` with a non-latest round ID — a standard pattern for staleness validation and historical price checks — receives a fabricated rsETH/USD price. Depending on whether the historical ETH/USD round is older or newer than the latest, the returned answer will be either inflated or deflated relative to the true historical rsETH/USD price. This can cause:

- Incorrect collateral valuations in lending protocols using rsETH as collateral.
- Incorrect liquidation thresholds being applied.
- Incorrect historical price assertions in any protocol that validates price continuity across rounds.

The LRT-rsETH protocol itself does not call `getRoundData` internally, so direct fund loss within the core protocol is not triggered. The impact is on downstream consumers of the feed, which is the intended use case of this contract.

---

### Likelihood Explanation

**Medium.** `RSETHPriceFeed` is explicitly designed as a Chainlink `AggregatorV3Interface` drop-in. Protocols integrating Chainlink feeds routinely call `getRoundData` to:
1. Validate that `answeredInRound >= roundId` (staleness check).
2. Retrieve the price at a specific historical round for TWAP or circuit-breaker logic.

Any such integration will silently receive incorrect data. No special conditions, admin access, or timing are required — the function is public and always returns the wrong answer for any `_roundId` that is not the current latest round.

---

### Recommendation

Store and expose a historical mapping of rsETH/ETH rates keyed by Chainlink round ID, or at minimum by timestamp, so that `getRoundData` can reconstruct the correct rsETH/USD price for a given historical round. If historical rsETH rates are not tracked on-chain, `getRoundData` should revert with an explicit `NotSupported()` error rather than silently returning a fabricated answer. The `latestRoundData()` path is correct and does not need to change.

---

### Proof of Concept

1. At time T₀, `LRTOracle.rsETHPrice` = 1.05 ETH, ETH/USD Chainlink round R₀ = $2000. True rsETH/USD at R₀ = $2100.
2. Time passes. At T₁, `LRTOracle.rsETHPrice` = 1.10 ETH, ETH/USD Chainlink round R₁ = $2100. True rsETH/USD at R₁ = $2310.
3. A lending protocol calls `RSETHPriceFeed.getRoundData(R₀)` to validate the price at round R₀.
4. The function returns: `answer = int256(1.10e18) * 2000e8 / 1e18 = 2200e8` — i.e., $2200.
5. The correct answer is $2100. The feed overstates the historical rsETH/USD price by ~4.8%, using the current rsETH/ETH rate (1.10) instead of the rate that existed at R₀ (1.05).
6. A lending protocol relying on this for a liquidation boundary or historical price check operates on incorrect data. [3](#0-2)

### Citations

**File:** contracts/oracles/RSETHPriceFeed.sol (L26-70)
```text
contract RSETHPriceFeed is AggregatorV3Interface {
    /// @notice Price feed for (ETH / USD) pair
    AggregatorV3Interface public immutable ETH_TO_USD;

    /// @notice rsETH oracle contract
    IRSETHOracle public immutable RS_ETH_ORACLE;

    string public description;

    /// @param ethToUSDAggregatorAddress the address of ETH / USD feed
    /// @param rsETHOracle the address of rsETHOracle contract
    /// @param description_ priceFeed description (RSETH / USD)
    constructor(address ethToUSDAggregatorAddress, address rsETHOracle, string memory description_) {
        ETH_TO_USD = AggregatorV3Interface(ethToUSDAggregatorAddress);
        RS_ETH_ORACLE = IRSETHOracle(rsETHOracle);

        description = description_;
    }

    function decimals() external view returns (uint8) {
        return ETH_TO_USD.decimals();
    }

    function version() external view returns (uint256) {
        return ETH_TO_USD.version();
    }

    function getRoundData(uint80 _roundId)
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.getRoundData(_roundId);

        answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
    }

    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
        answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
    }
```
