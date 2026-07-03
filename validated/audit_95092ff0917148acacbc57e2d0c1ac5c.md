Audit Report

## Title
`RSETHPriceFeed` Returns Corrupted Historical Prices and Non-Monotonic Round Answers Due to Decoupled `rsETHPrice` State - (File: contracts/oracles/RSETHPriceFeed.sol)

## Summary
`RSETHPriceFeed` implements `AggregatorV3Interface` but computes its answer by multiplying a historical ETH/USD price (from `ETH_TO_USD`) by the **current** `RS_ETH_ORACLE.rsETHPrice()` state variable. Because `rsETHPrice` is a stored value in `LRTOracle` that updates independently of ETH/USD Chainlink rounds, `getRoundData(_roundId)` returns a fabricated price that never existed on-chain, and `latestRoundData()` can return different answers for the same `roundId`.

## Finding Description
In `contracts/oracles/RSETHPriceFeed.sol`, both functions read `RS_ETH_ORACLE.rsETHPrice()` — a stored `uint256` state variable in `LRTOracle` updated by `updateRSETHPrice()` — at call time:

**`getRoundData` (lines 53–61):**
```solidity
(roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.getRoundData(_roundId);
answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
```
The historical ETH/USD price for `_roundId` is fetched correctly, but it is multiplied by the **current** `rsETHPrice`, not the ratio that existed at the time of that round. The result is a price that never existed on-chain.

**`latestRoundData` (lines 63–70):**
```solidity
(roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
```
The `roundId` is sourced entirely from the ETH/USD feed. When `updateRSETHPrice()` is called on `LRTOracle` (updating `rsETHPrice`) without a new ETH/USD round being posted, the same `roundId` will return a different `answer` on subsequent calls.

`rsETHPrice` in `LRTOracle` is a public state variable (`uint256 public override rsETHPrice`) updated by the permissionless `updateRSETHPrice()` function, which recomputes it from total ETH in the protocol. Any deposit, reward accrual, or EigenLayer position change can trigger a price update independently of ETH/USD Chainlink rounds.

No existing guard in `RSETHPriceFeed` prevents this: there is no internal round counter, no snapshot of historical rsETH prices, and no revert path for `getRoundData`.

## Impact Explanation
The contract is deployed as a drop-in `AggregatorV3Interface`-compatible price feed for rsETH/USD. It fails to deliver the invariants that interface promises: (1) a given `roundId` always maps to the same `answer`, and (2) `getRoundData` returns the price that existed at that historical round. This is a concrete instance of **Low — Contract fails to deliver promised returns**, as the contract advertises Chainlink feed compatibility but delivers broken round semantics and fabricated historical prices.

## Likelihood Explanation
`updateRSETHPrice()` is a public, permissionless function callable by any address. Any deposit into the protocol changes total ETH and thus `rsETHPrice`. ETH/USD Chainlink rounds update on a heartbeat (~1 hour) or deviation threshold (~0.5%). rsETH price updates can occur at any block. The divergence between `roundId` and `answer` is a routine occurrence, not an edge case, and requires no privileged access or special conditions to trigger.

## Recommendation
1. **For `getRoundData`**: Revert with `"No data present"` since historical rsETH/USD snapshots are not stored, consistent with the Chainlink spec for unavailable historical data.
2. **For `latestRoundData`**: Maintain an internal round counter that increments on every `rsETHPrice` update, decoupled from the ETH/USD feed's round system. Store the corresponding `answer`, `startedAt`, `updatedAt`, and `answeredInRound` at each update.
3. Alternatively, store a mapping of internal `roundId → (ethUsdRoundId, rsETHPriceSnapshot)` at each `rsETHPrice` update so historical queries return data that actually existed on-chain.

## Proof of Concept
1. At block B1: ETH/USD round = 100, ETH/USD price = $2000, `rsETHPrice` = 1.05e18 → `latestRoundData()` returns `(roundId=100, answer=2100e8, ...)`.
2. Call `updateRSETHPrice()` (permissionless) after a deposit increases TVL; `rsETHPrice` updates to 1.10e18. No new ETH/USD round is posted; `roundId` stays 100.
3. At block B2: `latestRoundData()` returns `(roundId=100, answer=2200e8, ...)` — same `roundId`, different `answer`.
4. Call `getRoundData(100)`: returns `$2000 × 1.10 = $2200` — a price that never existed at round 100 (actual was $2100).
5. Foundry fork test: fork mainnet, deploy `RSETHPriceFeed` pointing to real ETH/USD feed and a mock `IRSETHOracle` with mutable `rsETHPrice`. Assert `getRoundData(latestRoundId)` returns a different answer after mutating `rsETHPrice` without advancing the ETH/USD round. Assert `latestRoundData()` returns the same `roundId` with a different `answer` before and after the mutation.