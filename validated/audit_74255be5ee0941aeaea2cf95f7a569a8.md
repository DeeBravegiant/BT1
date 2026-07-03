Audit Report

## Title
`ChainlinkOracleForRSETHPoolCollateral::getRate` Staleness Check Is Dead Code Due to Deprecated `answeredInRound` Field - (File: `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol`)

## Summary

`ChainlinkOracleForRSETHPoolCollateral::getRate` performs its only staleness check using the deprecated Chainlink `answeredInRound` field, which on all modern OCR feeds is always equal to `roundId`, making the `StalePrice` revert permanently unreachable. No `updatedAt`-based timestamp check exists as a fallback. Stale collateral token prices flow unchecked into `RSETHPoolV3::deposit`, causing incorrect rsETH minting amounts — either over-minting (extracting value from the pool) or under-minting (depositor receives less than owed).

## Finding Description

In `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol` at lines 26–37, `getRate` calls `latestRoundData` and checks:

```solidity
if (answeredInRound < roundID) revert StalePrice();   // L30 — always false on OCR feeds
if (timestamp == 0) revert IncompleteRound();          // L31 — only catches uninitialized rounds
if (ethPrice <= 0) revert InvalidPrice();              // L32 — only catches zero/negative
```

On all current Chainlink OCR feeds, `answeredInRound` is always set equal to `roundId` by the aggregator, so the condition `answeredInRound < roundID` is structurally never true. The `StalePrice` revert is dead code. The `timestamp == 0` check only catches completely uninitialized rounds, not rounds where the price is simply outdated. There is no `block.timestamp - updatedAt > threshold` guard anywhere in the function.

This oracle is registered as the price source for supported collateral tokens via `supportedTokenOracle[token]` in `RSETHPoolV3` (L41). When `deposit(token, amount, referralId)` is called (L271–293), it invokes `viewSwapRsETHAmountAndFee(amount, token)` (L315–335), which fetches the collateral rate:

```solidity
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

A stale `tokenToETHRate` passes all three checks and directly determines how many rsETH tokens are minted.

## Impact Explanation

**Concrete allowed impact — Critical: Direct theft of any user funds / Protocol insolvency.**

If the Chainlink feed for a supported collateral token goes stale with a price lower than the true market price, any depositor (including an attacker monitoring for staleness) receives more rsETH than the deposited collateral is worth. The excess rsETH is backed by nothing, diluting all existing rsETH holders and constituting direct theft of pool value. Repeated exploitation across the staleness window leads to protocol insolvency.

**Secondary allowed impact — Low: Contract fails to deliver promised returns.**

If the stale price is higher than actual, depositors receive less rsETH than owed, meaning the contract fails to deliver its promised exchange rate.

## Likelihood Explanation

Chainlink feeds go stale during low-volatility periods (heartbeat not triggered), network congestion, or oracle node issues. No privileged access or special capability is required — any unprivileged user can call `deposit` at any time. An attacker only needs to monitor for oracle staleness and submit a deposit during the stale window. The staleness check is permanently non-functional, so every deposit during any stale period is affected. Likelihood is Medium.

## Recommendation

Replace the deprecated `answeredInRound` check with a `updatedAt` timestamp comparison against a configurable staleness threshold:

```solidity
uint256 public constant STALENESS_THRESHOLD = 3600; // configure per feed heartbeat

function getRate() public view returns (uint256) {
    (, int256 ethPrice,, uint256 updatedAt,) =
        AggregatorV3Interface(oracle).latestRoundData();

    if (updatedAt == 0) revert IncompleteRound();
    if (block.timestamp - updatedAt > STALENESS_THRESHOLD) revert StalePrice();
    if (ethPrice <= 0) revert InvalidPrice();

    return uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
}
```

Remove `roundID` and `answeredInRound` from the destructuring entirely.

## Proof of Concept

1. Deploy `ChainlinkOracleForRSETHPoolCollateral` pointing to a mock `AggregatorV3Interface` that returns `roundId = answeredInRound = 5`, `updatedAt = block.timestamp - 7200` (2 hours stale), `answer = 900e8` (10% below true price of `1000e8`).
2. Confirm `getRate()` returns `900e18 / 1e8 * 1e18` without reverting — all three checks pass: `answeredInRound (5) < roundID (5)` is false, `timestamp != 0`, `answer > 0`.
3. Register this oracle in `RSETHPoolV3` via `addSupportedToken`.
4. Call `RSETHPoolV3::deposit(token, 1e18, "")` as an unprivileged attacker.
5. `viewSwapRsETHAmountAndFee` computes `rsETHAmount = 1e18 * 900e18 / rsETHToETHrate` instead of the correct `1e18 * 1000e18 / rsETHToETHrate` — attacker receives ~11% more rsETH than the deposited collateral is worth, extracting value from the pool.
6. Repeat for the duration of the stale window; each deposit over-mints rsETH, progressively insolving the pool.