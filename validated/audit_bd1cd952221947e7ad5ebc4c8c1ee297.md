Audit Report

## Title
`RSETHPriceFeed.latestRoundData()` Returns Stale rsETH Price With Fresh `updatedAt` Timestamp - (File: contracts/oracles/RSETHPriceFeed.sol)

## Summary
`RSETHPriceFeed` computes the rsETH/USD price by multiplying the ETH/USD Chainlink price by `RS_ETH_ORACLE.rsETHPrice()`, but the returned `updatedAt` field is sourced exclusively from the ETH/USD Chainlink feed. Because `LRTOracle.rsETHPrice` is a state variable updated only when `updateRSETHPrice()` is called off-chain, any operational gap causes the rsETH component to become stale while `updatedAt` continues to appear fresh to downstream consumers. The contract fails to deliver its promised Chainlink-compatible freshness semantics.

## Finding Description
In `contracts/oracles/RSETHPriceFeed.sol` at lines 63–70:

```solidity
function latestRoundData()
    external
    view
    returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
{
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
}
```

`updatedAt` is assigned from `ETH_TO_USD.latestRoundData()` only. The `answer` silently incorporates `RS_ETH_ORACLE.rsETHPrice()`, which is the `rsETHPrice` state variable in `LRTOracle` (line 28 of `contracts/LRTOracle.sol`). That variable is only written when `updateRSETHPrice()` / `_updateRsETHPrice()` is called (lines 87–88 of `LRTOracle.sol`). The `IRSETHOracle` interface exposed to `RSETHPriceFeed` (line 22–24 of `RSETHPriceFeed.sol`) provides only `rsETHPrice()` — no timestamp. There is no `lastUpdated` field in `LRTOracle` and no staleness check inside `RSETHPriceFeed`. The identical flaw exists in `getRoundData()` at lines 53–61. Existing guards (the `pricePercentageLimit` circuit-breaker in `LRTOracle`) operate only at update time and provide no protection to downstream consumers reading a stale cached value.

## Impact Explanation
The contract fails to deliver its promised Chainlink-compatible behavior: `updatedAt` is supposed to represent when the returned `answer` was last valid, but it reflects only the ETH/USD leg. Downstream Chainlink consumers that gate on `updatedAt` staleness will treat a potentially hours-old rsETH price as current. This maps to the allowed impact: **Low — Contract fails to deliver promised returns, but doesn't lose value** (direct fund loss is mediated through external integrations, not this contract alone).

## Likelihood Explanation
`LRTOracle.updateRSETHPrice()` is a public function but requires an off-chain caller (keeper, bot, or manual operator). Any keeper failure, network disruption, or deliberate delay causes the rsETH component to go stale. The ETH/USD Chainlink feed updates independently every ~1 hour, so `updatedAt` will always appear fresh regardless of the rsETH oracle's age. This is a realistic operational scenario with no attacker action required — it is a passive design flaw triggered by normal operational gaps.

## Recommendation
Store the timestamp of the last `rsETHPrice` update in `LRTOracle` and expose it via the `IRSETHOracle` interface. In `RSETHPriceFeed`, return the **minimum** of the two `updatedAt` values:

```solidity
function latestRoundData() external view returns (...) {
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
    uint256 rsETHUpdatedAt = RS_ETH_ORACLE.lastUpdated();
    if (rsETHUpdatedAt < updatedAt) updatedAt = rsETHUpdatedAt;
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
}
```

Alternatively, add a configurable `maxRsETHStaleness` threshold and revert inside `RSETHPriceFeed` if the rsETH price is older than that threshold.

## Proof of Concept
1. Deploy `LRTOracle` and `RSETHPriceFeed` on a local fork.
2. Call `LRTOracle.updateRSETHPrice()` once to set an initial `rsETHPrice`.
3. Advance block time by 24 hours (`vm.warp(block.timestamp + 86400)`) without calling `updateRSETHPrice()` again.
4. The ETH/USD Chainlink feed updates normally (mock or fork confirms `updatedAt` ≈ `block.timestamp - 300`).
5. Call `RSETHPriceFeed.latestRoundData()`.
6. Observe: `updatedAt` is ~5 minutes old (passes any standard 1-hour staleness check), but `answer` is computed from a 24-hour-old `rsETHPrice`.
7. A lending protocol consuming this feed would accept the price as fresh and act on stale rsETH valuation. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
