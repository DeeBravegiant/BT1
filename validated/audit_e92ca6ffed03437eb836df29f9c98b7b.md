The code confirms the claim. Let me verify the other pool contracts as well.

Audit Report

## Title
Missing Chainlink Oracle Staleness Heartbeat Check Allows Stale Rate Exploitation in L2 Pool Deposits - (File: contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol)

## Summary
`ChainlinkOracleForRSETHPoolCollateral.getRate()` validates Chainlink `latestRoundData` with only a round-completeness check (`timestamp == 0`) and a deprecated round-ordering check (`answeredInRound < roundID`), but omits a heartbeat/staleness check (`block.timestamp - timestamp > maxStaleness`). When this oracle is used as the collateral token oracle in any L2 pool contract, an unprivileged depositor can exploit the stale price window to receive more wrsETH than the deposited collateral is worth, diluting the backing for all existing rsETH/wrsETH holders.

## Finding Description
`ChainlinkOracleForRSETHPoolCollateral.getRate()` at lines 26–37 reads `latestRoundData` and applies three guards:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

There is no check of the form `if (block.timestamp - timestamp > heartbeat) revert StalePrice();`.

Chainlink feeds update only when price moves beyond a deviation threshold **or** when the heartbeat period elapses. For L2 feeds such as wstETH/ETH on Optimism, Base, or Arbitrum, the heartbeat is up to 24 hours and the deviation threshold is 0.5–1%. During a low-volatility period the feed can be many hours stale while `answeredInRound == roundID`, so the existing `answeredInRound < roundID` guard passes. Additionally, on many L2 Chainlink deployments `answeredInRound` is always equal to `roundId`, making that guard entirely ineffective.

The deposit path in `RSETHPoolV3ExternalBridge.viewSwapRsETHAmountAndFee` (lines 449–452) uses the oracle directly:

```solidity
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

A stale `tokenToETHRate` that is higher than the real market rate inflates `rsETHAmount`, minting more wrsETH than the deposited collateral backs. The same pattern is present in `RSETHPool.sol` (line 343), `RSETHPoolV3.sol` (line 331), and `RSETHPoolNoWrapper.sol` (line 308).

## Impact Explanation
**High — Theft of unclaimed yield / share dilution of existing rsETH holders.**

When a depositor receives more wrsETH than the fair value of their collateral, the excess is backed by nothing. The rsETH/wrsETH exchange rate is diluted for all existing holders. The attacker deposits collateral at a stale (inflated) rate, receives excess wrsETH, and can immediately redeem or sell it. The `X%` excess is extracted from the pool's existing collateral backing, reducing the per-share value for all other holders. This constitutes theft of unclaimed yield from existing holders and matches the allowed High impact class.

## Likelihood Explanation
**Medium.** Chainlink L2 feeds (e.g., wstETH/ETH on Optimism, Base, Arbitrum) have heartbeats of up to 24 hours and deviation thresholds of 0.5–1%. During low-volatility periods the feed can be many hours stale while all existing guards pass. A sophisticated attacker monitors on-chain oracle `updatedAt` timestamps and the real market price off-chain; when the gap is profitable they execute the deposit. No privileged access is required — `deposit(token, amount, referralId)` is fully public and permissionless.

## Recommendation
Add a configurable maximum staleness parameter to `ChainlinkOracleForRSETHPoolCollateral` and enforce it in `getRate()`:

```solidity
uint256 public immutable maxStaleness; // e.g. 86400 + 600 for 24h feed

constructor(address _oracle, uint256 _maxStaleness) {
    oracle = _oracle;
    maxStaleness = _maxStaleness;
}

function getRate() public view returns (uint256) {
    (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
        AggregatorV3Interface(oracle).latestRoundData();

    if (answeredInRound < roundID) revert StalePrice();
    if (timestamp == 0) revert IncompleteRound();
    if (block.timestamp - timestamp > maxStaleness) revert StalePrice(); // ← add this
    if (ethPrice <= 0) revert InvalidPrice();

    uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
    return normalizedPrice;
}
```

Set `maxStaleness` to slightly above the feed's documented heartbeat (e.g., heartbeat + 10 minutes).

## Proof of Concept
1. The wstETH/ETH Chainlink feed on an L2 last updated 20 hours ago at a rate of `1.15e18`. The real market rate has since dropped to `1.13e18` (a ~1.7% drop, below the deviation threshold, so no update was triggered).
2. Call `ChainlinkOracleForRSETHPoolCollateral.getRate()`: `answeredInRound == roundID` passes; `timestamp != 0` passes; `ethPrice > 0` passes. Returns `1.15e18`. No staleness revert.
3. Attacker calls `RSETHPoolV3ExternalBridge.deposit(wstETH, 100e18, "")`.
4. Inside `viewSwapRsETHAmountAndFee(100e18, wstETH)`:
   - `tokenToETHRate = 1.15e18` (stale)
   - `rsETHToETHrate = getRate()` (rsETH oracle, correct)
   - `rsETHAmount = 100e18 * 1.15e18 / rsETHToETHrate` → attacker receives wrsETH backed by 115 ETH of value.
5. The actual wstETH deposited is worth only 113 ETH at market. The 2 ETH difference is extracted from the pool's existing backing, diluting all wrsETH holders.
6. Attacker repeats until the daily mint limit is exhausted or the oracle updates.

**Foundry fork test plan:** Fork the target L2 at a block where the wstETH/ETH feed `updatedAt` is >1 hour old. Warp `block.timestamp` forward by 20 hours (keeping `updatedAt` fixed). Call `deposit(wstETH, 100e18, "")` and assert that `rsETHAmount` exceeds the fair-value amount computed using the current market rate. Confirm no revert occurs.