Audit Report

## Title
Missing L2 Sequencer Uptime Check Enables Stale-Price Exploitation During Arbitrum Outage - (File: `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol`)

## Summary

`ChainlinkOracleForRSETHPoolCollateral.getRate()` calls Chainlink's `latestRoundData()` on Arbitrum without verifying that the L2 sequencer is live. During a sequencer outage, Chainlink feeds freeze at their last-known value; the existing guards (`answeredInRound < roundID`, `timestamp == 0`) do not trigger because no new round is opened. An unprivileged attacker can deposit collateral at the frozen (pre-outage) price and receive more rsETH than the deposited collateral is worth, diluting the backing of all existing rsETH holders.

## Finding Description

`ChainlinkOracleForRSETHPoolCollateral` is the oracle wrapper for collateral tokens (e.g., wstETH) in the Arbitrum pool. Its `getRate()` function at lines 26–37 fetches price via `latestRoundData()` and applies three guards:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

During an Arbitrum sequencer outage, Chainlink stops updating L2 feeds. The last round remains open with `answeredInRound == roundID`, so `StalePrice` never triggers. `timestamp` is non-zero (it was set before the outage), and `ethPrice` is positive. All three guards pass, and the function returns the frozen pre-outage price.

This oracle is consumed by `RSETHPool.viewSwapRsETHAmountAndFee(amount, token)` at line 343:

```solidity
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();
```

which is called by the public `deposit(address token, uint256 amount, string referralId)` function at lines 284–305. `RSETHPool` is explicitly annotated as the Arbitrum pool contract. The rsETH minted is:

```
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate
```

If `tokenToETHRate` is stale and inflated relative to the true market price, the attacker receives more rsETH than the deposited collateral is worth. No privileges are required; the `deposit` function is callable by any external address with only a `whenNotPaused` and `onlySupportedToken` check, neither of which is relevant to sequencer status.

## Impact Explanation

**High — Theft of unclaimed yield.**

When an attacker deposits collateral at a stale inflated price, the protocol issues rsETH backed by collateral worth less than the rsETH issued. This directly dilutes the rsETH/ETH backing ratio, reducing the redemption value for all existing rsETH holders. The value extracted by the attacker comes from the yield and backing that existing holders are entitled to. This matches the allowed impact "High. Theft of unclaimed yield."

## Likelihood Explanation

The Arbitrum sequencer has experienced documented outages. The protocol is explicitly deployed on Arbitrum. The attack requires no special permissions — any external address can call `deposit`. The attack window is open for the entire duration of any sequencer outage. The attacker only needs the collateral token's market price to fall during the outage (a realistic condition, since outages can last hours and markets continue moving off-chain). The attack is repeatable across any sequencer outage event.

## Recommendation

Add a sequencer uptime check in `ChainlinkOracleForRSETHPoolCollateral.getRate()` following the Chainlink L2 Sequencer Uptime Feed pattern:

```solidity
AggregatorV3Interface public immutable sequencerUptimeFeed;
uint256 public constant GRACE_PERIOD = 3600; // 1 hour

function getRate() public view returns (uint256) {
    (, int256 sequencerAnswer, uint256 startedAt,,) = sequencerUptimeFeed.latestRoundData();
    if (sequencerAnswer == 1 || block.timestamp - startedAt < GRACE_PERIOD) {
        revert SequencerDown();
    }

    (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
        AggregatorV3Interface(oracle).latestRoundData();

    if (answeredInRound < roundID) revert StalePrice();
    if (timestamp == 0) revert IncompleteRound();
    if (ethPrice <= 0) revert InvalidPrice();

    return uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
}
```

The `sequencerUptimeFeed` address should be set at construction time and correspond to the Chainlink Arbitrum Sequencer Uptime Feed (`0xFdB631F5EE196F0ed6FAa767959853A9F217697D` on Arbitrum mainnet). The grace period prevents exploitation immediately after sequencer recovery, when prices may still be catching up.

## Proof of Concept

1. Arbitrum sequencer goes offline. Chainlink L2 feeds freeze at last-known prices.
2. Off-chain, wstETH drops 15% in ETH terms (e.g., from 1.2 ETH to 1.02 ETH).
3. Attacker calls `RSETHPool.deposit(wstETH, 100e18, "")` on Arbitrum.
4. `viewSwapRsETHAmountAndFee` calls `ChainlinkOracleForRSETHPoolCollateral.getRate()`.
5. `latestRoundData()` returns the pre-outage price (1.2 ETH). `answeredInRound == roundID`, `timestamp != 0`, `ethPrice > 0` — all guards pass.
6. Attacker receives rsETH calculated at 1.2 ETH/wstETH instead of 1.02 ETH/wstETH — approximately 17.6% more rsETH than the deposited wstETH is worth.
7. Sequencer recovers. Attacker holds over-issued rsETH; the shortfall in backing is borne by all existing rsETH holders.

**Foundry fork test outline:**
```solidity
// Fork Arbitrum mainnet at a block just before a known sequencer outage
// Mock sequencerUptimeFeed to return answer=1 (down)
// Call RSETHPool.deposit(wstETH, 100e18, "")
// Assert rsETHAmount > expected amount at true market price
// Assert rsETH backing ratio has decreased
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** contracts/pools/RSETHPool.sol (L30-35)
```text
/// @title RSETHPool
/// @notice This contract is the pool contract for the rsETH pool on *Arbitrum*
/// @dev it differs from other RSETHPool contracts in other chains as it uses LZ_RSETH as the canonical rsETH token of
/// the chain.
/// @dev it was the first RSETHPool contract to be deployed in an L2 hence the legacy variables
contract RSETHPool is ERC20Upgradeable, AccessControlUpgradeable, ReentrancyGuardUpgradeable {
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

**File:** contracts/pools/RSETHPool.sol (L340-347)
```text
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```
