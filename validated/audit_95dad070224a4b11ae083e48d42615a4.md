The code confirms the claim. Let me verify the key facts:

**`RSETHPriceFeed.latestRoundData()`** (lines 63–70): `updatedAt` is taken directly from `ETH_TO_USD.latestRoundData()` with no adjustment for the rsETH price component's age. [1](#0-0) 

**`RSETHPriceFeed.getRoundData()`** (lines 53–61): Historical ETH/USD price is multiplied by the *current* `RS_ETH_ORACLE.rsETHPrice()`, producing a synthetic price that never existed at that round. [2](#0-1) 

**`LRTOracle.rsETHPrice`** is a plain state variable with no accompanying timestamp, updated only on explicit calls to `updateRSETHPrice()`. [3](#0-2) [4](#0-3) 

**`IRSETHOracle`** interface exposes only `rsETHPrice()` — no timestamp. [5](#0-4) 

All cited code matches the claim exactly. The finding is valid at Low severity ("Contract fails to deliver promised returns, but doesn't lose value").

---

Audit Report

## Title
`RSETHPriceFeed` Returns ETH/USD Staleness Metadata for a Composite rsETH/USD Price - (File: contracts/oracles/RSETHPriceFeed.sol)

## Summary
`RSETHPriceFeed` implements `AggregatorV3Interface` and computes an rsETH/USD price by multiplying the ETH/USD Chainlink price by `RS_ETH_ORACLE.rsETHPrice()`. However, the `updatedAt` and `answeredInRound` fields returned by `latestRoundData()` reflect only the ETH/USD feed's freshness. Because `LRTOracle.rsETHPrice` carries no update timestamp and `IRSETHOracle` exposes none, the composite price can be arbitrarily stale while `updatedAt` signals recency. Additionally, `getRoundData(_roundId)` multiplies a historical ETH/USD price by the *current* rsETH rate, producing a price that never existed at that round.

## Finding Description
`latestRoundData()` (lines 63–70 of `contracts/oracles/RSETHPriceFeed.sol`) fetches all five return values from `ETH_TO_USD.latestRoundData()` and then overwrites only `answer`:

```solidity
(roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
```

`updatedAt` is passed through unchanged from the ETH/USD feed. `LRTOracle` stores `rsETHPrice` as a plain `uint256` state variable (line 28) with no accompanying timestamp. `updateRSETHPrice()` (lines 87–89) is a public function that must be called explicitly; it is not triggered by the ETH/USD feed update. The `IRSETHOracle` interface (lines 22–24) exposes only `rsETHPrice()`, so `RSETHPriceFeed` has no way to obtain the rsETH price's age.

`getRoundData(_roundId)` (lines 53–61) compounds the problem: it fetches the ETH/USD price for an arbitrary historical round but multiplies it by the *current* `RS_ETH_ORACLE.rsETHPrice()`, returning a price that never existed at that round and `updatedAt` from that historical round — doubly misleading.

Existing checks: none. There is no guard in `RSETHPriceFeed` that compares the rsETH price age against any threshold, and `LRTOracle` records no timestamp for `rsETHPrice`.

## Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**

`RSETHPriceFeed` advertises full `AggregatorV3Interface` compliance. Any consumer applying the standard Chainlink staleness check (`block.timestamp - updatedAt < heartbeat`) will pass the check and accept a stale rsETH/USD composite price as fresh. The LRT-rsETH protocol itself does not directly lose funds, but the contract fails to deliver the correct staleness metadata it contractually promises as an `AggregatorV3Interface` implementor.

## Likelihood Explanation
The rsETH price update is not automatic. Any interval between keeper calls to `updateRSETHPrice()` — which is normal protocol operation — creates a window where `latestRoundData()` returns a stale `answer` with a fresh `updatedAt`. No attacker action is required; the condition arises from ordinary keeper scheduling. The ETH/USD Chainlink feed updates every few minutes, so `updatedAt` will almost always appear recent even when the rsETH component is hours old.

## Recommendation
1. Add a `lastUpdated` timestamp to `LRTOracle` that is set in `_updateRsETHPrice()` alongside `rsETHPrice`.
2. Expose it via `IRSETHOracle` (e.g., `lastUpdated() external view returns (uint256)`).
3. In `RSETHPriceFeed.latestRoundData()`, take the minimum of the two timestamps:
```solidity
uint256 rsETHLastUpdated = RS_ETH_ORACLE.lastUpdated();
if (rsETHLastUpdated < updatedAt) updatedAt = rsETHLastUpdated;
```
4. `getRoundData` should revert with `NotSupported()` since historical rsETH prices are not stored, or be clearly documented as returning only the ETH/USD historical price without a valid rsETH component.

## Proof of Concept
1. Deploy `RSETHPriceFeed` against a live ETH/USD Chainlink feed and `LRTOracle`.
2. Call `LRTOracle.updateRSETHPrice()` to set an initial rsETH price (e.g., 1.05e18).
3. Advance time 24 hours without calling `updateRSETHPrice()` again. The ETH/USD feed continues updating normally.
4. Call `RSETHPriceFeed.latestRoundData()`.
   - `updatedAt` is recent (e.g., 5 minutes ago, from the ETH/USD feed).
   - `answer` uses the 24-hour-old rsETH price.
5. Assert: `block.timestamp - updatedAt < 3600` passes, yet `answer` is computed from a 24-hour-old rsETH rate.

Foundry fork test outline:
```solidity
function test_staleRsETHWithFreshUpdatedAt() public {
    // snapshot rsETH price after initial update
    uint256 stalePrice = lrtOracle.rsETHPrice();
    // warp 24 hours, ETH/USD feed updates via mock but rsETH price is not updated
    vm.warp(block.timestamp + 24 hours);
    mockEthUsdFeed.setUpdatedAt(block.timestamp - 5 minutes);
    (, int256 answer,, uint256 updatedAt,) = rsETHPriceFeed.latestRoundData();
    // updatedAt is fresh but answer uses stale rsETH price
    assertLt(block.timestamp - updatedAt, 1 hours); // passes staleness check
    assertEq(uint256(answer), stalePrice * ethUsdPrice / 1e18); // stale composite
}
```

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
