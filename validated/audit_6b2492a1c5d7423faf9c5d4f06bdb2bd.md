Audit Report

## Title
`getRoundData` Uses Current rsETH/ETH Rate Instead of Historical Rate for `_roundId` — (File: `contracts/oracles/RSETHPriceFeed.sol`)

## Summary
`RSETHPriceFeed.getRoundData(_roundId)` implements `AggregatorV3Interface` and is expected to return the rsETH/USD price at a specific historical round. While it correctly fetches the historical ETH/USD price via `ETH_TO_USD.getRoundData(_roundId)`, it multiplies by `RS_ETH_ORACLE.rsETHPrice()`, which always returns the **current** rsETH/ETH rate, not the rate at `_roundId`. Any external protocol calling this function for historical price data receives a hybrid value that is incorrect whenever the rsETH/ETH rate has changed since that round.

## Finding Description
The confirmed implementation at `contracts/oracles/RSETHPriceFeed.sol` lines 53–61:

```solidity
function getRoundData(uint80 _roundId)
    external
    view
    returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
{
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.getRoundData(_roundId);
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
}
``` [1](#0-0) 

`RS_ETH_ORACLE` is typed as `IRSETHOracle`, whose only method is `rsETHPrice()` — a stateless current-price getter with no round-ID parameter: [2](#0-1) 

There is no mechanism in the contract to retrieve or store historical rsETH/ETH rates keyed by round. The `_roundId` parameter is passed only to `ETH_TO_USD.getRoundData`, leaving the rsETH/ETH leg permanently anchored to the present value. The `latestRoundData` function has the same structure but is semantically correct for its purpose. [3](#0-2) 

No guards, access controls, or staleness checks exist that would prevent an external caller from receiving the incorrect hybrid answer.

## Impact Explanation
The contract fails to deliver its documented return: an accurate historical rsETH/USD price for the requested round. Any downstream protocol using `getRoundData` for TWAP calculations, dispute windows, or historical price validation receives a materially wrong value proportional to the drift in rsETH/ETH since the queried round. No funds held in the LRT-rsETH protocol are directly at risk, placing this squarely in the **Low** allowed impact class: *Contract fails to deliver promised returns, but doesn't lose value.*

## Likelihood Explanation
`RSETHPriceFeed` is explicitly designed as a drop-in Chainlink-compatible feed for external integration. `getRoundData` is a standard part of `AggregatorV3Interface` and is routinely called by DeFi protocols for historical price lookups. Any integrator that calls this function after the rsETH/ETH rate has changed since the queried round will receive incorrect data. No special privileges or attacker capability are required — the function is `external view` and callable by anyone.

## Recommendation
Since no historical rsETH/ETH checkpoints are stored, the cleanest fix is to revert unconditionally in `getRoundData` to signal that historical round data is unsupported:

```solidity
function getRoundData(uint80 /*_roundId*/) external pure override
    returns (uint80, int256, uint256, uint256, uint80)
{
    revert("RSETHPriceFeed: historical round data not supported");
}
```

If historical accuracy is required, the contract must store rsETH/ETH price snapshots keyed by round ID whenever `updateRSETHPrice()` is called on `LRTOracle`, and look them up in `getRoundData`.

## Proof of Concept
1. Deploy `RSETHPriceFeed` pointing to a mock `ETH_TO_USD` feed and a mock `LRTOracle`.
2. Record round `R` with ETH/USD = 2000e8 and rsETH/ETH = 1.05e18 in the mock oracle.
3. Advance time; update `LRTOracle` so `rsETHPrice()` returns 1.10e18.
4. Call `RSETHPriceFeed.getRoundData(R)`.
5. Observe returned `answer` = 2000e8 × 1.10 = 2200e8 instead of the correct 2000e8 × 1.05 = 2100e8 — a 4.76% error.
6. Confirm `latestRoundData()` returns 2200e8 (correct for latest), while `getRoundData(R)` should return 2100e8 but returns 2200e8.

### Citations

**File:** contracts/oracles/RSETHPriceFeed.sol (L22-24)
```text
interface IRSETHOracle {
    function rsETHPrice() external view returns (uint256);
}
```

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
