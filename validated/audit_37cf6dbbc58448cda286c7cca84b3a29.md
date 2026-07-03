Audit Report

## Title
`RSETHPriceFeed` Returns ETH/USD Round Metadata Masking rsETH Price Staleness, and `getRoundData()` Always Returns Structurally Incorrect Historical Prices - (File: contracts/oracles/RSETHPriceFeed.sol)

## Summary
`RSETHPriceFeed` implements `AggregatorV3Interface` as the rsETH/USD price feed for external protocol consumption. In `latestRoundData()`, all round metadata fields (`roundId`, `updatedAt`, `answeredInRound`) are sourced from the ETH/USD Chainlink feed while `answer` is computed from the current `LRTOracle.rsETHPrice`, meaning standard heartbeat staleness checks validate ETH/USD freshness rather than rsETH oracle freshness. In `getRoundData(_roundId)`, a historical ETH/USD price is unconditionally multiplied by the **current** rsETH/ETH rate, producing a price that is never the actual rsETH/USD price at that historical round.

## Finding Description

**`latestRoundData()` staleness masking** (lines 63–70):

```solidity
function latestRoundData()
    external view
    returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
{
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
}
```

`updatedAt` reflects when the ETH/USD Chainlink feed last updated, not when `LRTOracle.rsETHPrice` was last written. `LRTOracle.rsETHPrice` is updated only when `updateRSETHPrice()` is called (line 87–89 of `LRTOracle.sol`). This function is public but has no on-chain heartbeat enforcement — it depends on off-chain keepers. No timestamp for the last rsETH price update is stored anywhere in `LRTOracle`. If `updateRSETHPrice()` has not been called for an extended period, `rsETHPrice` is stale, but `latestRoundData()` returns a recent `updatedAt` from the ETH/USD feed. Any consumer performing a standard heartbeat check (`block.timestamp - updatedAt < heartbeat`) incorrectly concludes the rsETH price is fresh.

**`getRoundData(_roundId)` always incorrect** (lines 53–61):

```solidity
function getRoundData(uint80 _roundId)
    external view
    returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
{
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.getRoundData(_roundId);
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
}
```

This fetches the ETH/USD price at historical round `_roundId` but multiplies it by the **current** `rsETHPrice`. The returned `answer` is therefore never the rsETH/USD price at that historical round — it is a synthetic value mixing a past ETH/USD price with a present rsETH/ETH rate. This is unconditional: every call to `getRoundData` for any historical round returns a structurally incorrect price.

The root cause is that `LRTOracle` stores `rsETHPrice` as a plain `uint256` with no associated update timestamp and no historical index, making it impossible to reconstruct the rsETH/ETH rate at any past point in time.

## Impact Explanation

**Low — Contract fails to deliver promised returns, but doesn't lose value.**

`RSETHPriceFeed` is deployed as the rsETH/USD price feed for external consumption and implements `AggregatorV3Interface`. It fails to deliver its core promise on two axes:

1. `latestRoundData()` can return a stale rsETH price with a fresh `updatedAt`, causing consumers' staleness guards to pass on outdated data.
2. `getRoundData()` unconditionally returns a price that is not the rsETH/USD price at the requested historical round — it is always incorrect for any round other than the current one.

No funds are lost from the LRT-rsETH protocol itself, but the contract fails to deliver the accurate price data it is designed and deployed to provide.

## Likelihood Explanation

The `getRoundData` historical price corruption is **unconditional** — it is always present for every historical round query, regardless of oracle freshness or keeper activity. The `latestRoundData` staleness masking requires a keeper gap (no call to `updateRSETHPrice()` for an extended period), which is a realistic operational condition given the absence of any on-chain enforcement. Both issues are reachable by any external caller with no special privileges.

## Recommendation

1. **`latestRoundData()`**: Store a `rsETHPriceUpdatedAt` timestamp in `LRTOracle` that is written alongside `rsETHPrice = newRsETHPrice` (line 313 of `LRTOracle.sol`). Return `min(ethToUsdUpdatedAt, rsETHPriceUpdatedAt)` as `updatedAt` so consumers' staleness checks reflect the freshness of the least-recently-updated component.

2. **`getRoundData()`**: Revert with a `NotSupported()` error, since accurate historical rsETH/USD prices cannot be reconstructed without a historical rsETH/ETH rate index. Alternatively, remove the function from the interface entirely and document that historical round data is not meaningful for this feed.

3. Consider storing a monotonically increasing `rsETHRoundId` in `LRTOracle` that increments on each `updateRSETHPrice()` call, and returning it as `answeredInRound` in `latestRoundData()`.

## Proof of Concept

**`getRoundData` — always incorrect (unconditional):**

1. At round `R` (historical), ETH/USD = 2000e8, rsETH/ETH = 1.02e18. True rsETH/USD at round R = 2040e8.
2. Today, rsETH/ETH = 1.08e18.
3. Call `RSETHPriceFeed.getRoundData(R)`.
4. Returned: `answer = 1.08e18 * 2000e8 / 1e18 = 2160e8` — not the actual 2040e8 at round R.
5. This is wrong for every historical round, every time.

**`latestRoundData` — stale rsETH price masked as fresh:**

1. At `T=0`, `LRTOracle.updateRSETHPrice()` is called. `rsETHPrice = 1.05e18`. ETH/USD `updatedAt = T`.
2. At `T+25h`, ETH/USD feed updates normally. ETH/USD `updatedAt = T+25h`. `rsETHPrice` has not been updated (keeper offline).
3. External protocol calls `RSETHPriceFeed.latestRoundData()`.
4. Returned: `updatedAt = T+25h` (from ETH/USD), `answer` uses 25-hour-old rsETH/ETH rate.
5. Protocol checks `block.timestamp - updatedAt ≈ 0 < heartbeat` → staleness check passes.
6. Protocol prices rsETH using a stale rate.

**Foundry fork test plan:**

```solidity
function test_getRoundData_alwaysIncorrect() public {
    uint80 historicalRound = /* any past round ID from ETH/USD feed */;
    (, int256 answer,,,) = priceFeed.getRoundData(historicalRound);
    (, int256 ethUsdAtRound,,,) = ETH_TO_USD.getRoundData(historicalRound);
    uint256 currentRsEthRate = RS_ETH_ORACLE.rsETHPrice();
    // answer == currentRsEthRate * ethUsdAtRound / 1e18
    // This is NOT the rsETH/USD price at historicalRound
    assertEq(answer, int256(currentRsEthRate) * ethUsdAtRound / 1e18);
    // True historical rsETH/USD is unknowable — no historical rsETH/ETH index exists
}
```