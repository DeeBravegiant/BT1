### Title
`RSETHPriceFeed.getRoundData` Returns Temporally Inconsistent Historical Price Data Due to Missing Round Validity Bounds — (File: `contracts/oracles/RSETHPriceFeed.sol`)

---

### Summary

`RSETHPriceFeed.getRoundData` accepts an arbitrary historical Chainlink round ID and combines the historical ETH/USD price from that round with the **current** rsETH/ETH rate from `RS_ETH_ORACLE`. No validation is performed on the round ID. The result is a corrupted, internally inconsistent price that is neither a valid historical rsETH/USD price nor the current one.

---

### Finding Description

`RSETHPriceFeed` implements `AggregatorV3Interface` and is deployed as the canonical rsETH/USD price feed consumed by Morpho (deployed at `0x4B9C66c2C0d3706AabC6d00D2a6ffD2B68A4E383`).

The `getRoundData` function is:

```solidity
function getRoundData(uint80 _roundId)
    external view
    returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
{
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.getRoundData(_roundId);
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
}
``` [1](#0-0) 

The function:
1. Fetches the historical ETH/USD price for any caller-supplied `_roundId` from Chainlink — which can be arbitrarily old (e.g., round 1 from years ago).
2. Multiplies it by the **current** `RS_ETH_ORACLE.rsETHPrice()` — always the live rsETH/ETH rate.
3. Returns the product as `answer` with the **historical** `updatedAt` timestamp from the old round. [2](#0-1) 

There is no check that `_roundId` is valid, recent, or within any acceptable range. The returned `answer` is not a valid historical rsETH/USD price — it is a hybrid of a stale ETH/USD price and the current rsETH/ETH rate. The `updatedAt` timestamp is from the historical ETH/USD round, making the data appear stale even though the rsETH/ETH component is current, or vice versa.

This is the direct analog of the `balanceAt(cleanedGeneration)` pattern: a function allows querying historical data at any arbitrary index without bounds checking, silently returning inconsistent/invalid data instead of reverting.

The `latestRoundData` function does not share this flaw — it correctly uses the latest ETH/USD price with the current rsETH/ETH rate. [3](#0-2) 

---

### Impact Explanation

Any external protocol or contract that calls `getRoundData` with an old round ID receives a corrupted rsETH/USD price. The `updatedAt` field is from the historical ETH/USD round, so staleness checks performed by the caller (e.g., `if (block.timestamp - updatedAt > threshold) revert`) will behave incorrectly — either falsely flagging a fresh price as stale, or accepting a corrupted price as fresh.

For Morpho, which uses this contract as its rsETH/USD oracle, any historical price query or dispute-resolution path that calls `getRoundData` would receive a price that is neither historically accurate nor currently accurate. This maps to: **Low — contract fails to deliver promised returns** (the `AggregatorV3Interface` contract promises accurate historical round data; this implementation does not deliver it).

---

### Likelihood Explanation

`getRoundData` is part of the standard `AggregatorV3Interface` and is callable by any external contract or user. While most DeFi protocols primarily use `latestRoundData`, some protocols and off-chain tooling call `getRoundData` for historical price verification, dispute resolution, or data integrity checks. The `RSETHPriceFeed` is a live production contract integrated with Morpho.

---

### Recommendation

Add validation in `getRoundData` to reject round IDs that are not the latest round, or revert entirely since the contract cannot reconstruct a valid historical rsETH/USD price (the historical rsETH/ETH rate is not stored on-chain):

```solidity
function getRoundData(uint80 /*_roundId*/) external pure override
    returns (uint80, int256, uint256, uint256, uint80)
{
    revert("getRoundData: historical rsETH/USD prices not supported");
}
```

Alternatively, document clearly that `getRoundData` does not return valid historical rsETH/USD prices and should not be used for that purpose.

---

### Proof of Concept

1. Chainlink ETH/USD round `R_old` from 2 years ago had `answer = 4000e8` (ETH at $4,000).
2. Current rsETH/ETH rate is `1.05e18` (rsETH worth 1.05 ETH).
3. Current ETH/USD is `2500e8` ($2,500).
4. Correct current rsETH/USD = `1.05 * 2500 = $2,625`.
5. Caller calls `getRoundData(R_old)`:
   - `answer` from Chainlink = `4000e8`
   - `RS_ETH_ORACLE.rsETHPrice()` = `1.05e18`
   - Returned `answer` = `1.05e18 * 4000e8 / 1e18` = `4200e8` ($4,200)
   - Returned `updatedAt` = timestamp from 2 years ago
6. The caller receives a price of $4,200 with a 2-year-old timestamp — 60% higher than the correct current price, with a timestamp that will fail any reasonable staleness check. [4](#0-3)

### Citations

**File:** contracts/oracles/RSETHPriceFeed.sol (L26-61)
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
