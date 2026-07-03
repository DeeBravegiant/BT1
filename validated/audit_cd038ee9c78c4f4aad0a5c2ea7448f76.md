Audit Report

## Title
Missing Time-Based Staleness Check in `getRate()` Allows Stale Collateral Price to Over-Mint wrsETH - (File: contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol)

## Summary

`ChainlinkOracleForRSETHPoolCollateral.getRate()` validates Chainlink price data with a round-ordering check (`answeredInRound < roundID`) and a zero-timestamp check, but never verifies that `block.timestamp - updatedAt` is within an acceptable staleness window. During an L2 sequencer outage, no new Chainlink rounds are opened, so `answeredInRound == roundID` throughout — the existing guard never fires — while the price silently ages. Any depositor can exploit the stale, inflated collateral price to receive more `wrsETH` than the deposited value warrants, extracting the surplus from the pool.

## Finding Description

`getRate()` in `ChainlinkOracleForRSETHPoolCollateral` fetches `latestRoundData()` and applies three guards:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L30-32
if (answeredInRound < roundID) revert StalePrice();   // round-ordering only
if (timestamp == 0) revert IncompleteRound();          // non-zero only
if (ethPrice <= 0) revert InvalidPrice();
```

The `timestamp` (`updatedAt`) field is fetched but never compared to `block.timestamp`. There is no check of the form `block.timestamp - timestamp > MAX_STALE_PERIOD`.

On L2 networks, when the sequencer goes offline, Chainlink oracles stop posting new rounds. Because no new round is opened, `answeredInRound` remains equal to `roundID` for the entire outage duration. The round-ordering guard (`answeredInRound < roundID`) therefore never triggers, and the stale price from the last pre-outage round is returned as if it were fresh.

This rate is consumed directly in the deposit pricing path:

```solidity
// contracts/pools/RSETHPoolV3.sol L331-334
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

If `tokenToETHRate` is stale and higher than the current market price (e.g., wstETH was 1.20 ETH at last update but has since dropped to 1.10 ETH), the depositor receives proportionally more `wrsETH` than the current fair value of their deposit. The same pattern is present in `RSETHPoolV3ExternalBridge.viewSwapRsETHAmountAndFee()`.

The SECURITY.md exclusion for "Incorrect data supplied by third-party oracles" does not apply here: the oracle is supplying technically correct data (the last recorded price); the vulnerability is the **contract's failure to validate the age of that data**, which is a code-level defect in this repository.

## Impact Explanation

**Critical — Direct theft of user funds.**

The over-minted `wrsETH` is redeemable 1:1 for `rsETH` on L1 via the wrapper. An attacker who deposits a collateral token while the price feed is stale receives excess `wrsETH` backed by no corresponding value. The shortfall is borne by existing pool liquidity providers. The magnitude scales with the price drift during the outage and the deposit size; a 9% price drop over a 24-hour heartbeat interval on a large deposit constitutes direct, concrete theft of pooled funds.

## Likelihood Explanation

**Medium.** L2 sequencer outages are documented historical events on Arbitrum and Optimism. During any such outage, Chainlink feeds stop updating while `answeredInRound` remains equal to `roundID`, so the existing round-based guard does not fire. The condition is externally observable: an attacker monitoring sequencer status and feed `updatedAt` timestamps can identify the window and submit a deposit immediately when the sequencer resumes, before the feed posts a new round. No privileged access is required; the `deposit(address token, uint256 amount, string referralId)` function is fully public.

## Recommendation

Add a configurable maximum staleness period and enforce it in `getRate()`:

```solidity
uint256 public constant MAX_STALE_PERIOD = 25 hours; // tune per feed heartbeat

function getRate() public view returns (uint256) {
    (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
        AggregatorV3Interface(oracle).latestRoundData();

    if (answeredInRound < roundID) revert StalePrice();
    if (timestamp == 0) revert IncompleteRound();
    if (block.timestamp - timestamp > MAX_STALE_PERIOD) revert StalePrice();
    if (ethPrice <= 0) revert InvalidPrice();

    uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
    return normalizedPrice;
}
```

The threshold should be set per-feed based on its documented heartbeat (e.g., 1 hour for ETH/USD on Arbitrum, 24 hours for some LST feeds). Additionally, consider integrating a Chainlink L2 sequencer uptime feed check before consuming any price data on L2 deployments.

## Proof of Concept

1. `ChainlinkOracleForRSETHPoolCollateral` is deployed on Arbitrum pointing at a wstETH/ETH Chainlink feed. Last recorded round: `wstETH = 1.20 ETH`, `updatedAt = T`, `roundID = answeredInRound = N`.
2. The Arbitrum sequencer goes offline. No new Chainlink rounds are posted. `roundID` and `answeredInRound` remain `N`.
3. The sequencer resumes. Real wstETH price has dropped to `1.10 ETH`, but the feed has not yet posted round `N+1`.
4. Attacker calls `RSETHPoolV3.deposit(wstETH, amount, referralId)`.
5. Inside `viewSwapRsETHAmountAndFee`, `tokenToETHRate = getRate()` returns the stale `1.20e18`.
6. `rsETHAmount = amountAfterFee * 1.20e18 / rsETHToETHrate` — attacker receives ~9% more `wrsETH` than fair value.
7. Attacker bridges `wrsETH` to L1, unwraps to `rsETH`, redeems for ETH, extracting the surplus from the pool.

**Foundry fork test plan:** Fork Arbitrum mainnet at a block immediately after a historical sequencer outage. Mock `latestRoundData()` to return a `updatedAt` timestamp 25+ hours in the past with `answeredInRound == roundID`. Call `deposit()` with a supported collateral token and assert that the minted `wrsETH` amount exceeds the fair-value equivalent. Confirm the check `block.timestamp - timestamp > MAX_STALE_PERIOD` would revert the call if added.