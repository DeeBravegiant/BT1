### Title
Stale `rsETHPrice` Silently Propagated as Fresh by `RSETHPriceFeed` When Protocol Is Paused — (File: `contracts/oracles/RSETHPriceFeed.sol`)

---

### Summary

`RSETHPriceFeed.latestRoundData()` computes the rsETH/USD price by multiplying the live Chainlink ETH/USD answer by `LRTOracle.rsETHPrice()` — a single stored value. The `updatedAt` timestamp it returns is sourced exclusively from the ETH/USD Chainlink feed. When `LRTOracle`'s downside-protection logic triggers a protocol pause (on a large price drop), `rsETHPrice` is frozen at its pre-drop value and cannot be refreshed because `updateRSETHPrice()` is gated by `whenNotPaused`. External lending protocols consuming `RSETHPriceFeed` receive a stale (inflated) rsETH/USD price paired with a fresh `updatedAt`, enabling over-borrowing against rsETH collateral.

---

### Finding Description

**Root cause — `RSETHPriceFeed` passes through only the ETH/USD staleness timestamp:** [1](#0-0) 

```solidity
function latestRoundData() external view returns (...) {
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
}
```

`updatedAt` is taken from the ETH/USD Chainlink feed (which updates every few minutes). The rsETH/ETH component — `RS_ETH_ORACLE.rsETHPrice()` — is a stored state variable in `LRTOracle` whose own update timestamp is never surfaced.

**Root cause — `updateRSETHPrice()` is blocked when the oracle is paused:** [2](#0-1) 

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

**Root cause — downside protection pauses the oracle and returns *without* updating `rsETHPrice`:** [3](#0-2) 

```solidity
if (isPriceDecreaseOffLimit) {
    if (!lrtDepositPool.paused()) lrtDepositPool.pause();
    if (!withdrawalManager.paused()) withdrawalManager.pause();
    _pause();
    return;   // rsETHPrice is NOT updated — it stays at the pre-drop value
}
```

The sequence is:
1. A significant price drop is detected.
2. `_pause()` is called on `LRTOracle`, `LRTDepositPool`, and `LRTWithdrawalManager`.
3. The function returns early — `rsETHPrice` is **not** written with the new (lower) value.
4. `updateRSETHPrice()` is now blocked by `whenNotPaused`.
5. `rsETHPrice` is permanently frozen at the inflated pre-drop value until an admin manually unpauses.

Meanwhile, `RSETHPriceFeed.latestRoundData()` continues to serve:
- `answer` = (stale, inflated rsETH/ETH) × (fresh ETH/USD) → inflated rsETH/USD
- `updatedAt` = ETH/USD Chainlink `updatedAt` → appears fresh to any consumer [4](#0-3) 

---

### Impact Explanation

Any external lending protocol (e.g., Aave, Compound, Morpho) that uses `RSETHPriceFeed` as the collateral price oracle for rsETH will:

- Observe a fresh `updatedAt` (from ETH/USD) and pass its own staleness check.
- Value rsETH at the pre-drop (inflated) price.
- Allow users to borrow more against rsETH collateral than the actual collateral is worth.

This enables direct theft of lender funds: an attacker deposits rsETH, borrows the maximum against the inflated price, and walks away with the difference when the loan becomes under-collateralized. The impact is **Critical** — direct theft of user funds from integrated lending protocols.

---

### Likelihood Explanation

**Medium-High.** The trigger condition — a price drop exceeding `pricePercentageLimit` — is an explicitly designed circuit-breaker, meaning it is expected to fire during adverse market events (e.g., EigenLayer slashing, LST depeg). The `pricePercentageLimit` is a configurable parameter; if set conservatively (e.g., 1–2%), even routine volatility can trigger it. The window of exploitation lasts until an admin manually unpauses the oracle, which may take hours. `RSETHPriceFeed` is purpose-built as a Chainlink-compatible feed for external integrations, making it highly likely to be consumed by lending protocols.

---

### Recommendation

1. **Surface the rsETH price update timestamp.** Store a `rsETHPriceUpdatedAt` timestamp in `LRTOracle` whenever `rsETHPrice` is written, and expose it. In `RSETHPriceFeed.latestRoundData()`, return `min(ethUpdatedAt, rsETHPriceUpdatedAt)` as `updatedAt` so consumers see the true staleness of the composite price.

2. **Allow price update during pause for the downside case.** In `_updateRsETHPrice()`, write the new (lower) `rsETHPrice` before calling `_pause()`, so the stored value reflects reality even while the protocol is paused.

3. **Add a staleness guard in `RSETHPriceFeed`.** Revert if `rsETHPriceUpdatedAt` is older than a configurable threshold (e.g., 24 hours).

---

### Proof of Concept

**Scenario:**

1. rsETH TVL drops sharply (e.g., EigenLayer slashing event). `newRsETHPrice` falls more than `pricePercentageLimit` below `highestRsethPrice`.
2. `_updateRsETHPrice()` calls `_pause()` and returns. `rsETHPrice` remains at, say, `1.05 ETH` (pre-drop). The true price is `0.95 ETH`.
3. `updateRSETHPrice()` reverts for all callers (`whenNotPaused`).
4. Attacker calls `RSETHPriceFeed.latestRoundData()`:
   - `updatedAt` = ETH/USD Chainlink `updatedAt` (e.g., 2 minutes ago — fresh).
   - `answer` = `1.05e18 * ETH_USD_price / 1e18` → inflated by ~10%.
5. Lending protocol accepts the price as fresh (staleness check passes on `updatedAt`).
6. Attacker deposits 1000 rsETH (true value: 950 ETH), borrows 1050 ETH worth of assets.
7. Attacker defaults; lending protocol is left with 950 ETH of collateral against 1050 ETH of debt — a 100 ETH loss per 1000 rsETH deposited. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** contracts/oracles/RSETHPriceFeed.sol (L26-70)
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

**File:** contracts/LRTOracle.sol (L270-282)
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
            }
```
