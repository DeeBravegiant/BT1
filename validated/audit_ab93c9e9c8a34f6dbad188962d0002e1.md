### Title
Missing Chainlink Data Validation in `RSETHPriceFeed` Propagates Stale/Invalid ETH/USD Prices into rsETH/USD Output - (File: `contracts/oracles/RSETHPriceFeed.sol`)

---

### Summary

`RSETHPriceFeed` is a composite Chainlink-compatible price feed that computes rsETH/USD by multiplying the ETH/USD Chainlink answer by the rsETH/ETH rate from `RS_ETH_ORACLE`. However, neither `latestRoundData()` nor `getRoundData()` perform any validation on the raw Chainlink ETH/USD data before using it. There are no staleness, negative-answer, or round-completeness checks. Stale or invalid ETH/USD data propagates directly into the rsETH/USD answer returned to consuming protocols such as Morpho.

---

### Finding Description

`RSETHPriceFeed.latestRoundData()` fetches ETH/USD from the Chainlink aggregator and immediately multiplies it by the rsETH/ETH rate:

```solidity
// contracts/oracles/RSETHPriceFeed.sol lines 63-70
function latestRoundData()
    external view
    returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
{
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
}
```

Three standard Chainlink safety checks are entirely absent:

| Check | Purpose | Present? |
|---|---|---|
| `updatedAt < block.timestamp - maxDelay` | Detect stale round | No |
| `answer <= 0` | Detect negative/zero price | No |
| `answeredInRound < roundId` | Detect incomplete round | No |

The same omissions exist in `getRoundData()` at lines 53–61.

By contrast, the external report's `ChainlinkAdapterOracle` (the reference vulnerability) at least enforced all three of these guards before returning a price. `RSETHPriceFeed` enforces none of them.

`ChainlinkPriceOracle.sol` (used internally to price LSTs for TVL) has the same problem at line 52, but `RSETHPriceFeed` is the externally-facing feed consumed by third-party protocols and is therefore the higher-impact surface.

---

### Impact Explanation

`RSETHPriceFeed` is deployed on Ethereum mainnet and is explicitly used by Morpho as the rsETH/USD oracle (README entry: `RSETHPriceFeed (Morph) | 0x4B9C66c2C0d3706AabC6d00D2a6ffD2B68A4E383`).

- **Stale ETH/USD price**: If the Chainlink ETH/USD feed has not been updated (e.g., during network congestion or a Chainlink outage), `updatedAt` will be old but the contract returns the answer without complaint. Morpho receives a stale rsETH/USD price and may execute incorrect liquidations or allow over-borrowing.
- **Negative/zero ETH/USD answer**: Chainlink feeds can return 0 or negative values during circuit-breaker events. The multiplication `int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18` produces a zero or negative rsETH/USD answer, which Morpho would consume as a valid price.
- **Incomplete round**: If `answeredInRound < roundId`, the answer is from a prior round and is unreliable.

Impact classification: **Medium — temporary freezing of funds / contract fails to deliver promised returns**, because incorrect rsETH/USD prices in Morpho can cause unjust liquidations or block legitimate borrows until the feed recovers.

---

### Likelihood Explanation

Chainlink ETH/USD feed staleness is a well-documented, recurring real-world event (e.g., during the March 2023 USDC depeg, multiple feeds lagged). The missing checks are a straightforward code omission with no compensating control anywhere in `RSETHPriceFeed`. Any caller of `latestRoundData()` — including Morpho's automated liquidation bots — triggers the vulnerable path.

---

### Recommendation

Add the three standard Chainlink validation guards inside both `latestRoundData()` and `getRoundData()` before using the returned `answer`:

```solidity
require(answer > 0, "Negative price");
require(updatedAt >= block.timestamp - MAX_DELAY, "Stale price");
require(answeredInRound >= roundId, "Incomplete round");
```

`MAX_DELAY` should be set to the Chainlink ETH/USD heartbeat (3600 seconds on mainnet).

---

### Proof of Concept

1. The Chainlink ETH/USD feed on mainnet stalls (no update for >1 hour, e.g., during network congestion).
2. A Morpho liquidation bot calls `RSETHPriceFeed.latestRoundData()`.
3. `ETH_TO_USD.latestRoundData()` returns the last known (stale) ETH/USD answer with an old `updatedAt` timestamp.
4. `RSETHPriceFeed` performs no staleness check and returns `int256(RS_ETH_ORACLE.rsETHPrice()) * staleAnswer / 1e18` as the rsETH/USD price.
5. If the stale price is lower than the true market price, Morpho treats rsETH collateral as worth less than it is and triggers liquidations on healthy positions, causing users to lose collateral.
6. If the stale price is higher than the true market price, Morpho allows users to borrow more than their collateral supports, creating bad debt.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** contracts/oracles/RSETHPriceFeed.sol (L26-43)
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
