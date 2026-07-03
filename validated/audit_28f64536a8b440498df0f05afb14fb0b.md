Audit Report

## Title
Missing Staleness and Validity Checks on ETH/USD Chainlink Feed Enables Stale Price Propagation — (`contracts/oracles/RSETHPriceFeed.sol`)

## Summary

`RSETHPriceFeed.latestRoundData()` fetches the ETH/USD price from Chainlink and multiplies it by `rsETHPrice`, but performs no validation on the returned `updatedAt` timestamp, `answeredInRound`, or `answer` sign. The raw, potentially stale `updatedAt` is forwarded verbatim to callers. Downstream lending/collateral protocols (e.g., Morpho) that enforce their own staleness guard on the returned `updatedAt` will revert on all price-dependent operations for the duration of any ETH/USD feed staleness event, temporarily freezing user funds.

## Finding Description

In `RSETHPriceFeed.latestRoundData()` ( [1](#0-0) ), the function calls `ETH_TO_USD.latestRoundData()` and passes all five return values through unchanged, only recomputing `answer`. There is no check on:

- `block.timestamp - updatedAt` vs. a heartbeat/staleness window
- `answeredInRound >= roundId` (round completeness)
- `answer > 0` (valid price)

The same omission exists in `getRoundData()`. [2](#0-1) 

By contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` — another oracle in the same codebase — explicitly checks `answeredInRound < roundID`, `timestamp == 0`, and `ethPrice <= 0` before using the Chainlink value. [3](#0-2) 

The `rsETHPrice` component is a stored state variable in `LRTOracle` with no on-chain timestamp attached. [4](#0-3)  It is not reflected in the returned `updatedAt`, so the composite answer can be a current rsETH/ETH rate multiplied by a stale ETH/USD price, with the stale `updatedAt` propagated to the consumer.

**Exploit path:**
1. The ETH/USD Chainlink feed goes stale (e.g., due to network congestion or a missed deviation threshold update) — a normal operational condition requiring no attacker action.
2. Any downstream protocol (Morpho, Aave, etc.) configured to use `RSETHPriceFeed` as its `AggregatorV3Interface`-compatible oracle calls `latestRoundData()`.
3. `RSETHPriceFeed` returns a non-zero `answer` with a stale `updatedAt` and does not revert.
4. The downstream protocol's own staleness guard (e.g., `block.timestamp - updatedAt > heartbeat`) triggers a revert.
5. All price-dependent operations — borrow, withdraw, liquidate — are blocked for all users of that market for the duration of the staleness event.

## Impact Explanation

**Temporary freezing of funds (Medium).** Users with positions in any lending/collateral protocol using `RSETHPriceFeed` as its oracle cannot borrow, withdraw, or be liquidated for the duration of the ETH/USD feed staleness event. The freeze is bounded by the staleness event duration (minutes to hours), not permanent, so Critical/permanent freeze is not warranted. This maps exactly to the allowed impact: *Medium — Temporary freezing of funds*.

## Likelihood Explanation

The ETH/USD Chainlink feed on mainnet has a 1-hour heartbeat and a 0.5% deviation threshold. Network congestion, Chainlink node issues, or a missed deviation update can cause the feed to go stale for minutes to hours. This is a realistic, non-adversarial, non-privileged condition. No attacker action is required — the staleness event alone is sufficient to trigger the freeze in any downstream protocol enforcing a staleness check. The condition is repeatable whenever the feed goes stale.

## Recommendation

Add a configurable `STALE_PRICE_THRESHOLD` (e.g., 3600 seconds for the ETH/USD 1-hour heartbeat) and validate the Chainlink response in both `latestRoundData()` and `getRoundData()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`: [5](#0-4) 

```solidity
uint256 public immutable STALE_PRICE_THRESHOLD;

// In latestRoundData():
(roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
if (block.timestamp - updatedAt > STALE_PRICE_THRESHOLD) revert StalePrice();
if (answeredInRound < roundId) revert IncompleteRound();
if (answer <= 0) revert InvalidPrice();
answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
```

## Proof of Concept

```solidity
// Foundry fork/unit test — no mainnet calls, uses vm.mockCall
function test_staleEthUsdPropagated() public {
    uint80 roundId = 1;
    int256 ethPrice = 3000e8;
    uint256 staleTimestamp = block.timestamp - 2 days;

    // Mock ETH/USD feed to return updatedAt = 2 days ago
    vm.mockCall(
        address(ETH_TO_USD),
        abi.encodeWithSelector(AggregatorV3Interface.latestRoundData.selector),
        abi.encode(roundId, ethPrice, staleTimestamp, staleTimestamp, roundId)
    );

    (, , , uint256 updatedAt, ) = priceFeed.latestRoundData();

    // RSETHPriceFeed returns stale updatedAt without reverting
    assertEq(updatedAt, staleTimestamp);
    assertTrue(block.timestamp - updatedAt > 1 hours);

    // Downstream consumer enforcing staleness (e.g., Morpho heartbeat check) reverts
    // → all borrows/withdrawals/liquidations blocked for duration of staleness event
    vm.expectRevert();
    morphoConsumer.borrow(marketParams, amount, shares, onBehalf, receiver);
}
```

The test confirms: `latestRoundData()` returns `answer > 0` with a stale `updatedAt` and does not revert inside `RSETHPriceFeed`, while any downstream consumer enforcing a staleness window is blocked.

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

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L26-37)
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
```

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```
