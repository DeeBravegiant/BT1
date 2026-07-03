Audit Report

## Title
Missing L2 Sequencer Uptime Check Enables Stale-Price Minting - (File: `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol`)

## Summary

`ChainlinkOracleForRSETHPoolCollateral.getRate()` calls `AggregatorV3Interface(oracle).latestRoundData()` on L2-deployed Chainlink feeds without verifying that the L2 sequencer is live. When the sequencer is offline, the feed freezes at its last recorded value; the three existing validity checks all pass on a stale-but-valid round, so `getRate()` returns an outdated (lower) price. Because rsETH accrues staking rewards monotonically, a depositor who calls `RSETHPoolV2.deposit()` during or immediately after sequencer downtime receives more wrsETH than the deposited ETH warrants, diluting the yield of all existing wrsETH holders.

## Finding Description

`ChainlinkOracleForRSETHPoolCollateral.getRate()` (lines 26–37) fetches the price and applies three guards:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

None of these guards detect sequencer-induced staleness. When the L2 sequencer is down, Chainlink stops updating the feed but the last round remains valid: `answeredInRound == roundID`, `timestamp != 0`, and `ethPrice > 0`. All three checks pass. There is also no heartbeat/staleness check (e.g., `block.timestamp - updatedAt > MAX_STALENESS`), and no sequencer uptime feed query anywhere in the contract suite.

The stale rate flows directly into `RSETHPoolV2.viewSwapRsETHAmountAndFee()`:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

A stale (lower) `rsETHToETHrate` produces a larger `rsETHAmount`. `deposit()` is permissionless, non-paused by default, and calls this function unconditionally.

## Impact Explanation

rsETH's ETH-denominated value increases monotonically as staking rewards accrue. During a sequencer outage the on-chain Chainlink rate is frozen below the true current rate. An attacker who deposits ETH immediately after the sequencer resumes (before the feed updates) mints wrsETH at the stale lower rate, receiving more tokens than the deposited ETH backs. This dilutes the yield that existing wrsETH holders have already accrued — a concrete instance of **theft of unclaimed yield (High)**, which is an explicitly allowed impact in the scope.

## Likelihood Explanation

Arbitrum, Optimism, Base, Scroll, and Linea have all experienced documented sequencer outages. The protocol is deployed on all of these chains (confirmed in README). `deposit()` is callable by any unprivileged user with no access control. An attacker monitoring sequencer status (e.g., via the Chainlink sequencer uptime feed itself) can time a deposit to the window between sequencer recovery and the next Chainlink heartbeat update, which can be minutes to hours depending on the feed's heartbeat interval. The attack is repeatable across every outage event.

## Recommendation

Add a sequencer uptime check in `ChainlinkOracleForRSETHPoolCollateral.getRate()` using the Chainlink L2 sequencer uptime feed, following the [Chainlink documentation](https://docs.chain.link/data-feeds/l2-sequencer-feeds#example-code):

```solidity
(, int256 answer, uint256 startedAt,,) = sequencerUptimeFeed.latestRoundData();
if (answer == 1) revert SequencerDown();
if (block.timestamp - startedAt < GRACE_PERIOD) revert GracePeriodNotOver();
```

Additionally, add a heartbeat staleness check: `if (block.timestamp - updatedAt > MAX_STALENESS) revert StalePrice();`

## Proof of Concept

1. Deploy on Arbitrum fork. Record current Chainlink rsETH/ETH rate = `1.05e18`.
2. Simulate sequencer downtime: freeze the Chainlink feed at `1.05e18` while true rsETH value increases to `1.06e18`.
3. Simulate sequencer recovery: the feed has not yet updated.
4. Attacker calls `RSETHPoolV2.deposit{value: 1 ether}("")`.
5. `getRate()` → `ChainlinkOracleForRSETHPoolCollateral.getRate()` returns `1.05e18` (stale). All three guards pass.
6. `rsETHAmount = 1e18 * 1e18 / 1.05e18 ≈ 0.9524 wrsETH`. Correct amount at true rate: `1e18 * 1e18 / 1.06e18 ≈ 0.9434 wrsETH`.
7. Attacker receives ~0.009 excess wrsETH per ETH, at the expense of existing holders' accrued yield.

Foundry fork test plan: fork Arbitrum mainnet, mock `latestRoundData()` to return a round with `updatedAt` set to `block.timestamp - 4 hours` (simulating a frozen feed post-outage), call `deposit()`, and assert that `rsETHAmount` exceeds the amount computed at the true current rate.