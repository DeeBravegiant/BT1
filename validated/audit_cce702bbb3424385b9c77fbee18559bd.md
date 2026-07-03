### Title
`RSETHPriceFeed.latestRoundData()` Returns ETH/USD Timestamp for a Composite rsETH/USD Price, Masking rsETH Component Staleness - (File: `contracts/oracles/RSETHPriceFeed.sol`)

### Summary
`RSETHPriceFeed.latestRoundData()` computes a composite rsETH/USD price by multiplying the ETH/USD Chainlink price by the rsETH/ETH rate from `LRTOracle`. However, the `updatedAt` timestamp it returns is sourced exclusively from the ETH/USD Chainlink feed. The timestamp of the rsETH/ETH component (`LRTOracle.rsETHPrice()`) is never fetched, validated, or returned. External protocols consuming this feed cannot detect when the rsETH/ETH component is stale.

### Finding Description
`RSETHPriceFeed` is a Chainlink `AggregatorV3Interface`-compatible contract that composes two independent price sources:

1. **ETH/USD** — from `ETH_TO_USD` (a live Chainlink feed with its own `updatedAt`)
2. **rsETH/ETH** — from `RS_ETH_ORACLE.rsETHPrice()` (the `LRTOracle` contract, updated only when `updateRSETHPrice()` is called)

In `latestRoundData()`:

```solidity
(roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
```

The `updatedAt` returned is the ETH/USD feed's timestamp. The rsETH/ETH price's own update time is never read, never compared to the ETH/USD timestamp, and never returned. The two timestamps are never validated against each other.

`LRTOracle.rsETHPrice` can become stale in multiple realistic scenarios:
- `LRTOracle` is paused (automatically triggered when price drops beyond `pricePercentageLimit`, or manually by a pauser)
- `updateRSETHPrice()` simply hasn't been called recently

When `LRTOracle` is paused, `updateRSETHPrice()` reverts due to `whenNotPaused`, so `rsETHPrice` freezes at its last value. Meanwhile, the ETH/USD Chainlink feed continues updating normally. `RSETHPriceFeed.latestRoundData()` will return a fresh `updatedAt` (from ETH/USD) paired with a stale rsETH/ETH rate, producing a composite rsETH/USD price that appears fresh but is not.

### Impact Explanation
External lending protocols (e.g., Aave, Compound) that use `RSETHPriceFeed` as a Chainlink-compatible oracle for rsETH collateral valuation perform staleness checks against the returned `updatedAt`. Because `updatedAt` reflects only the ETH/USD feed, these checks pass even when the rsETH/ETH component is hours or days stale.

- If `rsETHPrice` is stale-high (e.g., frozen before a real price drop): rsETH holders can over-borrow against inflated collateral, draining lending protocol reserves — theft of funds from the protocol.
- If `rsETHPrice` is stale-low (e.g., frozen before a real price increase): rsETH holders are unfairly liquidated at below-market collateral valuations — direct theft of user funds.

Both scenarios constitute direct theft of funds from rsETH holders or counterparties interacting through the supported `RSETHPriceFeed` path.

### Likelihood Explanation
`LRTOracle` has an automatic pause mechanism: when `newRsETHPrice < highestRsethPrice` by more than `pricePercentageLimit`, the oracle self-pauses. This is a normal operational event (e.g., during a market downturn or slashing event). During the pause, `rsETHPrice` is frozen. Any external protocol using `RSETHPriceFeed` during this window will receive a stale composite price with a fresh-looking `updatedAt`. No privileged access or attacker action is required to trigger the staleness — it is an expected protocol state.

### Recommendation
`latestRoundData()` should fetch and return the minimum (oldest) of the two component timestamps, and should revert or signal staleness if the rsETH/ETH component's update time is too old relative to a configurable heartbeat:

```solidity
function latestRoundData() external view returns (...) {
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
    uint256 rsETHUpdatedAt = RS_ETH_ORACLE.lastUpdatedAt(); // requires LRTOracle to expose this
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
    updatedAt = rsETHUpdatedAt < updatedAt ? rsETHUpdatedAt : updatedAt; // return the older timestamp
}
```

Alternatively, add a maximum age check for `rsETHPrice` and revert if it is stale.

### Proof of Concept

1. `LRTOracle.updateRSETHPrice()` is called at time `T0`, setting `rsETHPrice = P0`.
2. At time `T1 > T0`, a market event causes `newRsETHPrice < highestRsethPrice - pricePercentageLimit * highestRsethPrice`. `LRTOracle` auto-pauses; `rsETHPrice` is frozen at `P0`.
3. At time `T2 >> T1`, an external lending protocol calls `RSETHPriceFeed.latestRoundData()`.
4. The call returns `updatedAt = T2` (from the live ETH/USD Chainlink feed) and `answer = P0 * ethPrice_T2 / 1e18` (stale rsETH/ETH rate).
5. The lending protocol's staleness check passes (`T2 - updatedAt < heartbeat`), and it uses the stale composite price.
6. rsETH holders are liquidated at the stale (incorrect) price, or borrowers extract excess value against inflated collateral. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L270-281)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
```
