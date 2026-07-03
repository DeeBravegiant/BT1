Audit Report

## Title
Missing Time-Based Staleness Check Allows Block Stuffing to Prematurely Exhaust `dailyMintLimit` - (`contracts/pools/RSETHPoolV3.sol`)

## Summary

`RSETHPoolV3.deposit(address,uint256,string)` applies the `limitDailyMint` modifier, which computes `rsETHAmount` using an unchecked oracle rate from `viewSwapRsETHAmountAndFee`. Both oracle implementations (`CrossChainRateReceiver` and `ChainlinkOracleForRSETHPoolCollateral`) are push-based and lack time-based staleness validation. An attacker who stuffs blocks to delay oracle updates keeps an inflated `tokenToETHRate` in place, causing each deposit to over-consume `dailyMintAmount` and prematurely trigger `DailyMintLimitExceeded`, blocking all token deposits for up to 24 hours.

## Finding Description

**`limitDailyMint` modifier** (`RSETHPoolV3.sol` L96–125): calls `viewSwapRsETHAmountAndFee(amount, token)` and accumulates the result into `dailyMintAmount`. No staleness guard is applied to the oracle rate used in this computation.

**`viewSwapRsETHAmountAndFee`** (`RSETHPoolV3.sol` L331–334): reads `tokenToETHRate` directly from `IOracle(supportedTokenOracle[token]).getRate()` with no time-based validation:
```solidity
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**`CrossChainRateReceiver.getRate()`** (`CrossChainRateReceiver.sol` L103–105): returns the stored `rate` unconditionally. Although `lastUpdated` is stored at L97, it is never checked in `getRate()`. The rate is only refreshed when the LayerZero endpoint calls `lzReceive`, a regular on-chain transaction that can be excluded by block stuffing.

**`ChainlinkOracleForRSETHPoolCollateral.getRate()`** (`ChainlinkOracleForRSETHPoolCollateral.sol` L26–37): the only staleness guard is `answeredInRound < roundID`, which detects an incomplete round but not elapsed time. If block stuffing prevents Chainlink keepers from submitting a new round, the last completed round's data passes this check indefinitely regardless of age.

**Attack path:**
1. A supported token's true ETH value begins falling; the oracle update transaction would lower `tokenToETHRate`.
2. Attacker stuffs blocks with high-gas transactions to exclude the oracle update transaction.
3. `tokenToETHRate` remains at the pre-depeg (inflated) value.
4. Each `deposit(token, amount, ...)` call computes an inflated `rsETHAmount`, consuming `dailyMintAmount` faster than it should.
5. `dailyMintAmount + rsETHAmount > dailyMintLimit` triggers `DailyMintLimitExceeded`, blocking all further token deposits until `getCurrentDay() > lastMintDay` resets `dailyMintAmount` to 0 (up to 24 hours).

## Impact Explanation

Legitimate users cannot deposit the affected supported token for up to 24 hours. No funds already in the contract are at risk. The concrete impact is a temporary, attacker-induced denial of the deposit service for the affected token, matching the allowed impact: **Low. Block stuffing.**

## Likelihood Explanation

Block stuffing is economically viable on L2 chains (where `RSETHPoolV3` is deployed) due to lower block gas limits and cheaper gas prices compared to Ethereum mainnet. The attacker requires no privileged access — `deposit` is a public, permissionless function. The oracle update cadence is predictable for both Chainlink heartbeat intervals and periodic LayerZero pushes. The attack requires no external compromise and is repeatable each day.

## Recommendation

Add a time-based staleness check in `viewSwapRsETHAmountAndFee` or within the oracle wrappers themselves:

- **`CrossChainRateReceiver`**: expose `lastUpdated` (already stored at L16) and enforce a maximum age in `getRate()`: `if (block.timestamp - lastUpdated > maxStaleness) revert StaleRate();`
- **`ChainlinkOracleForRSETHPoolCollateral`**: add a heartbeat check alongside the existing round check: `if (block.timestamp - timestamp > heartbeat) revert StalePrice();`

This ensures a stale rate causes a revert rather than silently inflating `rsETHAmount` and consuming the daily limit.

## Proof of Concept

Deploy `RSETHPoolV3` with a `MockOracle` returning an inflated `tokenToETHRate` (simulating a stale, pre-depeg rate held in place by block stuffing). Set `dailyMintLimit` to a fixed value. Call `deposit(token, amount, "")` in a loop. Observe that the limit is exhausted after fewer deposits than would occur with the correct (lower) rate, and that subsequent calls revert with `DailyMintLimitExceeded`. The PoC provided in the submission correctly models this: with a stale rate of `1.1e18` vs. a correct rate of `0.9e18`, the limit is hit after ~9 deposits instead of ~11, confirming premature exhaustion. A Foundry fork test against the deployed L2 instance with a real Chainlink feed whose last update is artificially aged (by warping `block.timestamp` forward past the heartbeat without submitting a new round) would reproduce the same result against live oracle state.