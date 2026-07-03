Audit Report

## Title
Missing L2 Sequencer Uptime Check Allows Stale Collateral Pricing During Arbitrum Outage - (File: contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol)

## Summary
`ChainlinkOracleForRSETHPoolCollateral.getRate()` calls Chainlink's `latestRoundData()` without verifying the Arbitrum sequencer is live. During a sequencer outage, the feed stops updating but the round counter also freezes, so the existing staleness guard (`answeredInRound < roundID`) never triggers. Any depositor can exploit the stale price via the L1 delayed inbox to receive more `wrsETH` than their collateral is worth, extracting value from the pool's reserves at the expense of other participants.

## Finding Description
`ChainlinkOracleForRSETHPoolCollateral.getRate()` (lines 26–37) applies three guards after calling `latestRoundData()`:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [1](#0-0) 

None of these guards detect sequencer downtime. When the Arbitrum sequencer is offline, Chainlink L2 feeds stop updating but the round ID also stops advancing, so `answeredInRound == roundID` still holds — the `StalePrice` revert is never triggered. The feed silently returns the last pre-downtime price.

`RSETHPool` is explicitly the Arbitrum L2 pool: [2](#0-1) 

The public `deposit(address token, uint256 amount, string referralId)` function calls `viewSwapRsETHAmountAndFee(amount, token)`, which fetches the collateral rate via `IOracle(supportedTokenOracle[token]).getRate()`: [3](#0-2) 

This oracle call resolves to `ChainlinkOracleForRSETHPoolCollateral.getRate()`, which returns the stale pre-outage price. The pool then mints `wrsETH` proportional to the inflated collateral valuation, transferring excess tokens to the attacker from the pool's own `wrsETH` balance. [4](#0-3) 

## Impact Explanation
**Medium — Temporary freezing of funds / theft of unclaimed yield.**

During a sequencer outage, if the real market price of the collateral token has fallen below the last Chainlink-reported price, an attacker deposits collateral at the inflated stale rate and receives more `wrsETH` than the collateral is worth. The excess `wrsETH` is extracted from the pool's reserves, reducing the amount available to honest depositors. The pool's accounting is corrupted for the duration of the outage. This matches the allowed impact of "Temporary freezing of funds" and "Theft of unclaimed yield."

## Likelihood Explanation
Arbitrum sequencer outages have occurred historically. No special permissions are required — `deposit()` is a public function with no access control beyond `whenNotPaused`. The attacker only needs to monitor sequencer status and submit a deposit transaction via the Arbitrum L1 delayed inbox (which bypasses the sequencer) while the outage is active. The attack window is bounded by the outage duration but is repeatable across any outage event. [5](#0-4) 

## Recommendation
Follow the [Chainlink L2 Sequencer Uptime Feeds](https://docs.chain.link/data-feeds/l2-sequencer-feeds) pattern. Add a sequencer uptime feed address as an immutable to `ChainlinkOracleForRSETHPoolCollateral` and check it at the top of `getRate()` before the existing staleness checks:

```solidity
(, int256 answer, uint256 startedAt,,) = sequencerUptimeFeed.latestRoundData();
if (answer != 0) revert SequencerDown();
if (block.timestamp - startedAt < GRACE_PERIOD) revert GracePeriodNotOver();
```

This should be inserted before the existing guards in `getRate()`. [1](#0-0) 

## Proof of Concept
1. Arbitrum sequencer goes offline. The wstETH/ETH Chainlink feed last reported `1.15 ETH` per wstETH; the real market price has since dropped to `1.05 ETH`.
2. Attacker submits a `deposit(wstETH, 100e18, "")` call to `RSETHPool` via the Arbitrum L1 delayed inbox, bypassing the offline sequencer.
3. `RSETHPool.deposit()` calls `viewSwapRsETHAmountAndFee(100e18, wstETH)` → `IOracle(supportedTokenOracle[wstETH]).getRate()` → `ChainlinkOracleForRSETHPoolCollateral.getRate()` → `latestRoundData()` returns stale `1.15e18`. All three guards pass (`answeredInRound == roundID`, `timestamp != 0`, `price > 0`).
4. Pool computes `rsETHAmount = 100e18 * 1.15e18 / rsETHToETHrate`, minting LP tokens valued at `115 ETH` worth of rsETH for a deposit actually worth `105 ETH`.
5. Sequencer resumes; attacker holds excess `wrsETH` representing ~10 ETH of extracted value from the pool's reserves.

**Foundry fork test plan:** Fork Arbitrum mainnet at a block just before a historical sequencer outage. Deploy `ChainlinkOracleForRSETHPoolCollateral` pointing at the live wstETH/ETH feed. Warp `block.timestamp` forward to simulate the outage window (feed timestamp frozen). Call `getRate()` and assert it returns the pre-outage price without reverting. Then call `RSETHPool.deposit()` with wstETH and assert the attacker receives more `wrsETH` than a fair-price deposit would yield. [1](#0-0) [6](#0-5)

### Citations

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L26-37)
```text
    function getRate() public view returns (uint256) {
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
    }
```

**File:** contracts/pools/RSETHPool.sol (L31-34)
```text
/// @notice This contract is the pool contract for the rsETH pool on *Arbitrum*
/// @dev it differs from other RSETHPool contracts in other chains as it uses LZ_RSETH as the canonical rsETH token of
/// the chain.
/// @dev it was the first RSETHPool contract to be deployed in an L2 hence the legacy variables
```

**File:** contracts/pools/RSETHPool.sol (L284-305)
```text
    function deposit(
        address token,
        uint256 amount,
        string memory referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedToken(token)
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
    }
```

**File:** contracts/pools/RSETHPool.sol (L343-346)
```text
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```
