Audit Report

## Title
Unvalidated Chainlink `latestRoundData()` Return Values in `ChainlinkPriceOracle.getAssetPrice()` Allow Stale/Invalid Price to Corrupt rsETH Exchange Rate - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` and silently discards `roundId`, `updatedAt`, and `answeredInRound`, accepting any returned `price` — including zero or a stale value — without validation. Because `updateRSETHPrice()` is publicly callable and `pricePercentageLimit` defaults to zero (no deviation guard on deployment), any caller can lock a stale or zero price into the `rsETHPrice` storage slot, causing over-minting (protocol insolvency) or under-minting (theft of depositor yield) for all subsequent deposits.

## Finding Description

`contracts/oracles/ChainlinkPriceOracle.sol` L49–55:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

Three return values are discarded with no guards:
- `answeredInRound >= roundId` (round completeness / staleness)
- `updatedAt > 0` (incomplete round)
- `price > 0` (valid price)

The same codebase already implements all three guards in `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol` L30–32:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The unguarded oracle feeds into the publicly callable `updateRSETHPrice()` (`LRTOracle.sol` L87–89, `public whenNotPaused`), which calls `_updateRsETHPrice()` → `_getTotalEthInProtocol()` → `getAssetPrice(asset)` (L339) → `ChainlinkPriceOracle.getAssetPrice()`. The computed price is then persisted at `LRTOracle.sol` L313: `rsETHPrice = newRsETHPrice`.

The `pricePercentageLimit` guard in `_updateRsETHPrice()` (L256–274) is the only partial mitigation, but it is initialized to zero in `initialize()` and only set by admin via `setPricePercentageLimit()`. With `pricePercentageLimit == 0`, the condition `pricePercentageLimit > 0 && ...` is always false, meaning no deviation check is applied at all. Even when non-zero, the guard only catches deviations exceeding the configured threshold; stale prices within the threshold are written to storage unchecked.

The corrupted `rsETHPrice` is then consumed by every deposit at `LRTDepositPool.sol` L520:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

## Impact Explanation

**Scenario A — stale price lower than actual (heartbeat lag during a price rally):** `totalETHInProtocol` is underestimated; `rsETHPrice` is written too low. When the feed recovers, `getAssetPrice(asset)` returns the correct higher value while `rsETHPrice` remains stale-low, causing `rsethAmountToMint` to be inflated. Depositors receive excess rsETH, diluting existing holders — **protocol insolvency (Critical)**.

**Scenario B — stale price higher than actual (feed not yet reflecting a depeg):** `rsETHPrice` is written too high; depositors receive fewer rsETH than entitled — **theft of depositor yield (High)**.

**Scenario C — `price == 0` (deprecated or broken feed, `pricePercentageLimit == 0`):** `totalETHInProtocol` collapses to near-zero; `rsETHPrice` is written near-zero; all subsequent deposits mint a massive rsETH amount — **protocol insolvency (Critical)**.

## Likelihood Explanation

- `updateRSETHPrice()` requires no privileges; any EOA or contract can call it.
- Chainlink LST/ETH feeds have documented heartbeat intervals (e.g., 24 h); a stale window exists every cycle.
- `pricePercentageLimit` is zero by default, removing the only partial mitigation until an admin explicitly configures it.
- On L2 deployments, sequencer downtime can freeze feed updates for hours while the contract remains callable.
- The attack is repeatable every heartbeat cycle with no privileged access required.

Likelihood: **Medium**.

## Recommendation

Mirror the validation already present in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0)            revert IncompleteRound();
    if (price <= 0)                revert InvalidPrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Additionally, add a per-feed configurable `maxStaleness` threshold: `if (block.timestamp - updatedAt > maxStaleness) revert StalePrice();`.

## Proof of Concept

**Setup:** Deploy on a mainnet fork. Assume `pricePercentageLimit == 0` (default). stETH/ETH Chainlink feed last updated 23 h 59 m ago at 0.998 ETH; true current price is 1.002 ETH (not yet reflected in feed).

1. Attacker calls `LRTOracle.updateRSETHPrice()`.
2. `_getTotalEthInProtocol()` calls `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns stale 0.998 ETH (no revert).
3. `totalETHInProtocol` is underestimated; `rsETHPrice` is written to storage ~0.4% below fair value (L313).
4. Chainlink heartbeat fires; feed updates to 1.002 ETH.
5. Attacker calls `LRTDepositPool.depositAsset(stETH, largeAmount, 0, "")`.
6. `getRsETHAmountToMint` computes: `largeAmount * 1.002e18 / stale_low_rsETHPrice` → attacker receives ~0.8% excess rsETH.
7. Attacker redeems via withdrawal path, extracting value from existing holders.

**Foundry fork test plan:**
```solidity
function testStaleOracleCorruptsRsETHPrice() public {
    // 1. Fork mainnet; warp to just before heartbeat fires
    // 2. Assert pricePercentageLimit == 0
    // 3. Call lrtOracle.updateRSETHPrice() — records stale rsETHPrice
    // 4. Mock Chainlink feed to return updated (higher) price
    // 5. Deposit large stETH amount; record rsethAmountToMint
    // 6. Call updateRSETHPrice() again with correct price; record fair rsethAmountToMint
    // 7. Assert step-5 amount > step-6 amount (excess minting confirmed)
}
```