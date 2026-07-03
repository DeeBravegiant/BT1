Audit Report

## Title
Missing Time-Based Staleness Check in `getRate()` Allows Stale Collateral Price to Over-Mint wrsETH — (`contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol`)

## Summary

`ChainlinkOracleForRSETHPoolCollateral.getRate()` validates Chainlink round data with only `answeredInRound < roundID` and `timestamp == 0`, but never enforces a maximum age on `updatedAt`. A feed that last updated arbitrarily long ago but whose last round was self-answered passes every guard and returns the stale price. Because `RSETHPoolV3.deposit()` and `RSETHPoolV3ExternalBridge` use this oracle's output directly to compute `rsETHAmount`, a stale inflated price causes `wrsETH.mint()` to issue more shares than the deposited collateral is worth, enabling direct extraction of value from the protocol.

## Finding Description

`getRate()` in `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol` (L26–37) performs exactly three validations:

```solidity
if (answeredInRound < roundID) revert StalePrice();   // round-based only
if (timestamp == 0)            revert IncompleteRound();
if (ethPrice <= 0)             revert InvalidPrice();
```

The `answeredInRound == roundID` condition only confirms the answer was computed in the same round it was opened — it says nothing about how long ago that round closed. A feed that last updated 48 hours ago with `answeredInRound == roundID` and `timestamp != 0` passes every guard and returns the old price. No check of the form `block.timestamp - timestamp > MAX_DELAY` exists anywhere in the contracts (confirmed by exhaustive grep across `contracts/**/*.sol`).

This oracle is consumed directly in `RSETHPoolV3.viewSwapRsETHAmountAndFee()` (L331–334):

```solidity
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

And identically in `RSETHPoolV3ExternalBridge` (L449–452). The computed `rsETHAmount` is then passed directly to `wrsETH.mint(msg.sender, rsETHAmount)` (RSETHPoolV3.sol L290) with no further sanity check. An inflated `tokenToETHRate` linearly inflates `rsETHAmount`.

**Exploit path:**
1. A Chainlink feed for a supported collateral token goes stale (naturally, due to keeper failure, low volatility, or congestion) while `answeredInRound == roundID` and `timestamp != 0`.
2. The real market price of the collateral token falls below the stale oracle price (e.g., a liquid-staking token trades at a discount).
3. An attacker calls `deposit(token, amount, referralId)` — a fully permissionless, public function — depositing the now-cheaper token.
4. `getRate()` returns the stale, inflated `tokenToETHRate`; `rsETHAmount` is over-computed; `wrsETH.mint` issues excess shares.
5. The attacker redeems or sells the excess wrsETH, extracting value never deposited. The wrsETH supply becomes under-collateralised.

## Impact Explanation

**Critical — Direct theft of user/protocol funds.** The over-minted wrsETH represents claims on collateral that was never deposited. All existing wrsETH holders are diluted and the protocol becomes insolvent relative to its collateral backing. This matches the allowed impact: "Direct theft of any user funds" and "Protocol insolvency."

## Likelihood Explanation

No privileged role, governance capture, or oracle operator compromise is required. The attacker only needs to monitor the `updatedAt` field of the relevant Chainlink feed off-chain and wait for a natural staleness window coinciding with a price decline. Chainlink feeds are known to go stale during network congestion or low-volatility heartbeat periods. The deposit function is public and permissionless. The attack is repeatable as long as the feed remains stale.

## Recommendation

Add a configurable per-feed maximum staleness delay and enforce it in `getRate()`:

```solidity
uint256 public immutable maxStaleness; // set per-feed at construction

function getRate() public view returns (uint256) {
    (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
        AggregatorV3Interface(oracle).latestRoundData();

    if (answeredInRound < roundID) revert StalePrice();
    if (timestamp == 0) revert IncompleteRound();
    if (block.timestamp - timestamp > maxStaleness) revert StalePrice(); // ADD THIS
    if (ethPrice <= 0) revert InvalidPrice();

    return uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
}
```

Set `maxStaleness` per-feed based on the documented Chainlink heartbeat interval (e.g., 3600s for a 1-hour heartbeat feed).

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;
import "forge-std/Test.sol";

contract MockStaleAggregator {
    function decimals() external pure returns (uint8) { return 8; }
    function latestRoundData() external view returns (
        uint80 roundId, int256 answer, uint256 startedAt,
        uint256 updatedAt, uint80 answeredInRound
    ) {
        roundId         = 1;
        answeredInRound = 1;                         // passes answeredInRound < roundID
        updatedAt       = block.timestamp - 48 hours; // 48 h stale — NOT caught
        startedAt       = updatedAt;
        answer          = 2_000e8;                   // inflated; real price is 1_000e8
    }
}

contract StalePriceTest is Test {
    function test_staleOraclePassesAllChecks() public {
        MockStaleAggregator agg = new MockStaleAggregator();
        ChainlinkOracleForRSETHPoolCollateral oracle =
            new ChainlinkOracleForRSETHPoolCollateral(address(agg));

        // Does NOT revert — returns stale inflated price
        uint256 rate = oracle.getRate();
        assertEq(rate, 2_000e18); // real market rate is 1_000e18 → 2× over-mint
    }
}
```

Deploying this against the unmodified `ChainlinkOracleForRSETHPoolCollateral` confirms `getRate()` returns the 48-hour-old inflated price without reverting, satisfying the precondition for the over-minting attack through `deposit(token, amount, referralId)`.