### Title
`RSETHPriceFeed.getRoundData` Returns Fabricated Historical Price by Mixing Historical ETH/USD with Current rsETH/ETH Rate - (File: contracts/oracles/RSETHPriceFeed.sol)

### Summary

`RSETHPriceFeed` implements `AggregatorV3Interface` and is deployed as a live Chainlink-compatible price feed on Morph (`0x4B9C66c2C0d3706AabC6d00D2a6ffD2B68A4E383`). Its `getRoundData` function fetches a historical ETH/USD price for a specific Chainlink round but applies the **current** `RS_ETH_ORACLE.rsETHPrice()` to compute the answer. This is structurally identical to the WLFI bug: just as `getPastVotes` used a historical checkpoint but omitted the custom vesting logic that `getVotes` applied, `getRoundData` uses a historical ETH/USD price but omits the historical rsETH/ETH rate, substituting the current one instead.

### Finding Description

`RSETHPriceFeed` computes rsETH/USD by multiplying the ETH/USD price by the rsETH/ETH rate:

```solidity
// latestRoundData — correct: current ETH/USD × current rsETH/ETH
answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;

// getRoundData — incorrect: historical ETH/USD × CURRENT rsETH/ETH
(roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.getRoundData(_roundId);
answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
```

`RS_ETH_ORACLE.rsETHPrice()` is a single stored scalar in `LRTOracle` — it holds only the most recently updated value and has no historical snapshot mechanism. When `getRoundData(_roundId)` is called for a past round, the ETH/USD component is correctly historical, but the rsETH/ETH multiplier is always the present-day value. The result is a price that never existed at that round.

The root cause is that `LRTOracle` stores only `rsETHPrice` (a single `uint256`) with no checkpoint history, so `getRoundData` structurally cannot retrieve the rsETH/ETH rate that was valid at the requested round's timestamp.

### Impact Explanation

`RSETHPriceFeed` is a production Chainlink-compatible feed. External protocols on Morph (e.g., lending markets, collateral managers) that consume it and call `getRoundData` for historical price verification will receive a fabricated answer. Concretely:

- If rsETH/ETH has appreciated since the queried round, the returned historical answer is **inflated** (current rsETH/ETH > historical rsETH/ETH), making the price appear higher in the past than it actually was.
- If rsETH/ETH has depreciated, the returned historical answer is **deflated**.

Any circuit-breaker, price-deviation check, or liquidation logic that compares a current price against a historical round's price via `getRoundData` will operate on incorrect data. The contract fails to deliver the historical price it promises to implement per `AggregatorV3Interface`.

**Impact: Low — Contract fails to deliver promised returns, but doesn't lose value directly within LRT-rsETH itself.**

### Likelihood Explanation

`RSETHPriceFeed` is already deployed on Morph and is publicly callable. Any external protocol that integrates it and calls `getRoundData` for historical price checks is immediately affected. Since rsETH/ETH monotonically increases over time (it accrues staking rewards), the inflation direction of the error is consistent and predictable, making the discrepancy grow larger over time.

### Recommendation

Since `LRTOracle` has no historical rsETH price snapshots, `getRoundData` cannot be correctly implemented. The recommended fix mirrors the WLFI mitigation: explicitly revert in `getRoundData` to prevent consumers from relying on incorrect data:

```solidity
function getRoundData(uint80 /*_roundId*/)
    external
    pure
    override
    returns (uint80, int256, uint256, uint256, uint80)
{
    revert("RSETHPriceFeed: historical round data unavailable");
}
```

Alternatively, add a historical rsETH/ETH price snapshot mechanism to `LRTOracle` (keyed by Chainlink round ID or timestamp) so `getRoundData` can apply the correct rsETH/ETH rate for the requested round.

### Proof of Concept

1. At time T0, rsETH/ETH = 1.02 (stored in `LRTOracle.rsETHPrice`). Chainlink ETH/USD round R0 records ETH = $3000. Correct rsETH/USD at R0 = 1.02 × $3000 = $3060.
2. Time passes. rsETH/ETH grows to 1.05. Chainlink advances to round R1.
3. A consumer calls `RSETHPriceFeed.getRoundData(R0)`:
   - `ETH_TO_USD.getRoundData(R0)` returns `answer = $3000` (correct historical ETH/USD).
   - The contract then computes: `answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18` = 1.05 × $3000 = **$3150**.
4. The returned historical price is **$3150**, not the correct **$3060** — a 3% inflation of the historical price.
5. Any protocol comparing the current rsETH/USD price against this inflated historical baseline will underestimate the true price appreciation, potentially suppressing circuit-breaker triggers. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/oracles/RSETHPriceFeed.sol (L53-61)
```text
    function getRoundData(uint80 _roundId)
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.getRoundData(_roundId);

        answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
    }
```

**File:** contracts/oracles/RSETHPriceFeed.sol (L63-70)
```text
    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
        answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
    }
```

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```
