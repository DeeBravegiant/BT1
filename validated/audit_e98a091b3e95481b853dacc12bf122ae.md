### Title
Unsafe `uint256` to `int256` Cast in `RSETHPriceFeed` Returns Corrupted Price Data - (File: contracts/oracles/RSETHPriceFeed.sol)

### Summary
`RSETHPriceFeed.getRoundData()` and `latestRoundData()` cast the `uint256` return value of `RS_ETH_ORACLE.rsETHPrice()` directly to `int256` without bounds checking. In Solidity 0.8.x, explicit casts do **not** revert on overflow — they silently truncate. If `rsETHPrice()` ever returns a value exceeding `type(int256).max`, the cast silently wraps to a negative `int256`, causing the price feed to return a corrupted (negative or wildly incorrect) rsETH/USD price to any consuming protocol.

### Finding Description
In `RSETHPriceFeed.sol`, both `getRoundData()` and `latestRoundData()` compute the final answer as:

```solidity
answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
```

`RS_ETH_ORACLE.rsETHPrice()` returns a `uint256`. The cast `int256(uint256_value)` is unchecked in Solidity 0.8.x — it silently truncates the high bit, producing a negative `int256` if the value exceeds `2^255 - 1`. No `SafeCast` or range assertion is applied before the cast.

### Impact Explanation
`RSETHPriceFeed` is a Chainlink `AggregatorV3Interface`-compatible contract consumed by external DeFi protocols (e.g., lending markets) to price rsETH/USD collateral. A corrupted negative `answer` would cause those protocols to:
- Reject rsETH as collateral (treating it as worthless or invalid)
- Trigger mass liquidations at incorrect prices
- Allow under-collateralized borrowing if the consuming protocol does not validate sign

This maps to **Low — contract fails to deliver promised returns** given the near-zero likelihood.

### Likelihood Explanation
Current `rsETHPrice()` is approximately `1.05e18` (rsETH priced slightly above 1 ETH in 18-decimal fixed point). `type(int256).max ≈ 5.79e76`. The value would need to grow by ~58 orders of magnitude to overflow, which is practically impossible under any realistic market condition. Likelihood is therefore very low, but the unsafe pattern is present and unguarded.

### Recommendation
Replace the bare cast with OpenZeppelin's `SafeCast.toInt256()`:

```solidity
import { SafeCast } from "@openzeppelin/contracts/utils/math/SafeCast.sol";

answer = SafeCast.toInt256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
```

This will revert rather than silently corrupt the price if the value ever exceeds `int256` range.

### Proof of Concept
1. Suppose `RS_ETH_ORACLE.rsETHPrice()` returns `2^255` (e.g., due to a bug or extreme manipulation in the oracle).
2. `int256(2^255)` silently wraps to `type(int256).min` (a large negative number) — no revert in Solidity 0.8.x for explicit casts.
3. `answer = type(int256).min * ethUsdPrice / 1e18` produces a large negative `int256`.
4. Any Chainlink consumer calling `latestRoundData()` receives a negative `answer`, corrupting downstream pricing logic. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** contracts/oracles/RSETHPriceFeed.sol (L22-24)
```text
interface IRSETHOracle {
    function rsETHPrice() external view returns (uint256);
}
```

**File:** contracts/oracles/RSETHPriceFeed.sol (L60-60)
```text
        answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
```

**File:** contracts/oracles/RSETHPriceFeed.sol (L68-70)
```text
        (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
        answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
    }
```
