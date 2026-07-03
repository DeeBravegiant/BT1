Audit Report

## Title
Stale rsETH/ETH Rate Masked by Fresh ETH/USD `updatedAt` in `RSETHPriceFeed.latestRoundData()` — (File: contracts/oracles/RSETHPriceFeed.sol)

## Summary
`RSETHPriceFeed.latestRoundData()` computes a composite rsETH/USD price by multiplying a cached `rsETHPrice` state variable by a live ETH/USD Chainlink answer, but returns only the ETH/USD feed's `updatedAt` timestamp. Because `rsETHPrice` is updated only when `updateRSETHPrice()` is externally called, the returned `updatedAt` never reflects the age of the rsETH/ETH component. Any consumer applying a standard Chainlink staleness check will be deceived into treating a stale composite answer as fresh.

## Finding Description
`RSETHPriceFeed.latestRoundData()` at lines 63–70 of `contracts/oracles/RSETHPriceFeed.sol` fetches all five return values — including `updatedAt` — exclusively from `ETH_TO_USD.latestRoundData()`, then overwrites only `answer` with the composite value:

```solidity
(roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
``` [1](#0-0) 

`RS_ETH_ORACLE.rsETHPrice()` resolves to `LRTOracle.rsETHPrice`, a plain public state variable with no associated timestamp: [2](#0-1) 

This variable is only written inside `_updateRsETHPrice()`, which is triggered by the public, permissionless (but not automatic) `updateRSETHPrice()`: [3](#0-2) 

The assignment `rsETHPrice = newRsETHPrice` stores no timestamp alongside the new value: [4](#0-3) 

**Block stuffing exploit path:** On a low-basefee L2 where `RSETHPriceFeed` is deployed, an attacker fills consecutive blocks with high-gas transactions to exclude `updateRSETHPrice()` calls. During this window, `rsETHPrice` remains frozen at its last value while the Chainlink ETH/USD feed continues to update normally. Every call to `latestRoundData()` returns an `updatedAt` equal to the ETH/USD feed's latest update — potentially seconds old — while the rsETH/ETH component is arbitrarily stale. A consumer checking `block.timestamp - updatedAt < threshold` passes the staleness check and consumes the mispriced composite answer.

No existing guard in `RSETHPriceFeed` checks the age of `rsETHPrice`; the contract is entirely `view` and performs no staleness validation on the rsETH component. [5](#0-4) 

## Impact Explanation
The composite rsETH/USD answer can be arbitrarily stale in its rsETH/ETH component while appearing fresh to any standard Chainlink consumer. Downstream lending markets or other protocols using `RSETHPriceFeed` as a price source and relying on `updatedAt` for staleness detection cannot distinguish a fresh answer from a stale one, enabling mispriced collateral valuation or liquidation decisions. This matches the allowed impact **Low — Block stuffing**.

## Likelihood Explanation
Block stuffing is expensive but feasible on L2s with low block gas costs. The staleness mismatch exists unconditionally — even without block stuffing, any natural gap between `updateRSETHPrice()` calls creates a window where `updatedAt` misrepresents the composite answer's true age. The attack is repeatable as long as the attacker can sustain block stuffing, and the structural flaw persists regardless.

## Recommendation
1. Store a `rsETHPriceUpdatedAt` timestamp in `LRTOracle` alongside `rsETHPrice` and set it to `block.timestamp` every time `_updateRsETHPrice()` writes `rsETHPrice`.
2. In `RSETHPriceFeed.latestRoundData()`, return `min(ethToUSD_updatedAt, rsETHPriceUpdatedAt)` as `updatedAt` so consumers see the true age of the composite answer.
3. Optionally, add an explicit staleness revert in `latestRoundData()` if `rsETHPriceUpdatedAt` is older than a configurable threshold.

## Proof of Concept
Deploy `RSETHPriceFeed` against a mock `LRTOracle` (fixed `rsETHPrice = 1.05e18`) and a mock ETH/USD feed. Advance time by `G` seconds without calling `updateRSETHPrice()`, then update the mock ETH/USD feed's `updatedAt` to `block.timestamp`. Call `feed.latestRoundData()` — the returned `updatedAt` equals the ETH/USD feed's fresh timestamp despite the rsETH component being `G` seconds stale. The invariant `block.timestamp - updatedAt <= 1 hours` passes for any `G`, confirming the staleness is invisible to consumers. The Foundry invariant test provided in the submission directly demonstrates this: for any `G` in `[1 hours, 30 days]`, `updatedAt` reflects only ETH/USD freshness, not rsETH/ETH freshness.

### Citations

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

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```
