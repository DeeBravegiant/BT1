Audit Report

## Title
`RSETHPriceFeed.latestRoundData()` Returns ETH/USD `updatedAt` for a Composite Answer That Includes a Separately-Staleable rsETH/ETH Component — (`contracts/oracles/RSETHPriceFeed.sol`)

## Summary
`RSETHPriceFeed.latestRoundData()` computes a composite rsETH/USD price by multiplying `rsETHPrice` (stored in `LRTOracle`) by the ETH/USD Chainlink answer, but returns only the ETH/USD feed's `updatedAt` timestamp. Because `LRTOracle` stores no update timestamp for `rsETHPrice`, the returned `updatedAt` structurally cannot reflect rsETH/ETH staleness. Any downstream consumer applying a standard Chainlink staleness check will pass even when the rsETH/ETH component is arbitrarily stale.

## Finding Description
In `contracts/oracles/RSETHPriceFeed.sol` lines 63–70, `latestRoundData()` destructures all five return values — including `updatedAt` — from `ETH_TO_USD.latestRoundData()`, then overwrites only `answer` with the composite value:

```solidity
(roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData(); // line 68
answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;                              // line 69
```

`RS_ETH_ORACLE.rsETHPrice()` reads the `rsETHPrice` storage variable in `LRTOracle` (line 28), which is only updated when `updateRSETHPrice()` is called (line 87). That function is `public whenNotPaused` with no further access control, meaning it relies on external keepers. `LRTOracle` stores no `lastUpdatedAt` for `rsETHPrice`; `_updateRsETHPrice()` (lines 214–316) writes only to `rsETHPrice`, `highestRsethPrice`, and fee-accounting variables — never a timestamp. The `RSETHPriceFeed` therefore has no source from which to derive rsETH/ETH freshness, and the returned `updatedAt` is exclusively the ETH/USD heartbeat timestamp.

Exploit path:
1. `updateRSETHPrice()` is not called for an extended period (keeper failure, high gas, or block stuffing on Morph L2 where the contract is deployed).
2. ETH/USD moves materially; Chainlink's own infrastructure continues pushing ETH/USD updates normally.
3. `latestRoundData()` returns a fresh `updatedAt` (ETH/USD just updated) while `rsETHPrice` is frozen at its last stored value.
4. The composite `answer` diverges from the true rsETH/USD price in the ETH/USD direction while the rsETH/ETH leg is stale.
5. A consumer checking `block.timestamp - updatedAt < threshold` passes the staleness guard and consumes the mispriced composite value.

No existing check in `RSETHPriceFeed` guards against this: the contract is a pure `view` function with no staleness revert, no rsETH timestamp read, and no `min()` aggregation.

## Impact Explanation
The contract is deployed as a Chainlink-compatible price feed (`RSETHPriceFeed` on Morph, per the README). The `updatedAt` field in `AggregatorV3Interface` carries the semantic promise that it reflects when the returned `answer` was last updated. Because `answer` is composite but `updatedAt` is single-source, the contract fails to deliver that promised invariant. This matches the allowed Low impact: **"Contract fails to deliver promised returns, but doesn't lose value"** — no funds are lost within the LRT-rsETH contracts themselves, but downstream consumers receive a structurally misleading freshness signal. The block-stuffing vector on Morph L2 additionally matches the allowed Low impact: **"Block stuffing."**

## Likelihood Explanation
The staleness mismatch is structural and present at all times — it does not require an active attack. Any period of keeper inactivity (gas spikes, network congestion, keeper bugs) produces the condition. The block-stuffing path is additionally feasible on Morph L2 where block gas limits are lower than Ethereum mainnet, reducing the cost of preventing `updateRSETHPrice()` from landing. `updateRSETHPrice()` has no access control beyond `whenNotPaused`, so no privileged capability is required to create the gap — only the absence of a successful keeper call.

## Recommendation
1. Add a `rsETHPriceUpdatedAt` timestamp variable to `LRTOracle` and set it to `block.timestamp` inside `_updateRsETHPrice()` at the point where `rsETHPrice` is written (line 313).
2. Expose it via the `IRSETHOracle` interface.
3. In `RSETHPriceFeed.latestRoundData()`, replace the raw `updatedAt` from ETH/USD with `Math.min(ethToUSD_updatedAt, rsETHPriceUpdatedAt)`, so consumers see the staleness of the least-fresh component.
4. Optionally, add an explicit revert inside `latestRoundData()` if `rsETHPriceUpdatedAt` is older than a configurable threshold, preventing consumption of a stale composite price entirely.

## Proof of Concept
Foundry fork test on Morph:
1. Record `rsETHPrice_0 = LRTOracle.rsETHPrice()` and `updatedAt_0` from `RSETHPriceFeed.latestRoundData()` at block N.
2. Advance time by more than the rsETH/ETH staleness threshold (e.g., 24 hours) without calling `updateRSETHPrice()` (simulate by warping `block.timestamp` or filling blocks with dummy transactions to block keeper calls).
3. Allow the Chainlink ETH/USD mock to update normally (or use a real fork where ETH/USD updates).
4. Call `RSETHPriceFeed.latestRoundData()` → assert `block.timestamp - updatedAt < staleness_threshold` passes (ETH/USD is fresh).
5. Assert `LRTOracle.rsETHPrice() == rsETHPrice_0` (rsETH/ETH is frozen).
6. Assert `|answer - true_rsETH_USD| / true_rsETH_USD > safe_deviation` where `true_rsETH_USD` uses a freshly computed rsETH/ETH value.
7. Confirm no staleness check on `updatedAt` alone would have caught the divergence — demonstrating the contract fails to deliver the promised `updatedAt` semantics for its composite answer.