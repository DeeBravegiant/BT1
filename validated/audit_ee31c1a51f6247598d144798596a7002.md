### Title
Chainlink Oracle Used on L2 Pools Without Sequencer Uptime Check Enables Stale-Price Exploitation - (File: contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol)

---

### Summary

`ChainlinkOracleForRSETHPoolCollateral` is used by all L2 pool variants (`RSETHPool`, `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPoolNoWrapper`) to price collateral tokens when minting wrsETH. The contract calls Chainlink's `latestRoundData()` without verifying that the L2 sequencer is online and has been running for a minimum grace period. No sequencer uptime check exists anywhere in the codebase.

---

### Finding Description

`ChainlinkOracleForRSETHPoolCollateral.getRate()` fetches the collateral token price via `AggregatorV3Interface(oracle).latestRoundData()`:

```solidity
function getRate() public view returns (uint256) {
    (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
        AggregatorV3Interface(oracle).latestRoundData();

    if (answeredInRound < roundID) revert StalePrice();
    if (timestamp == 0) revert IncompleteRound();
    if (ethPrice <= 0) revert InvalidPrice();

    uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
    return normalizedPrice;
}
``` [1](#0-0) 

The three checks present (`answeredInRound < roundID`, `timestamp == 0`, `ethPrice <= 0`) do not protect against the L2 sequencer downtime scenario. On L2 networks (Arbitrum, Optimism, Base, etc.), when the sequencer goes offline, Chainlink oracle nodes stop submitting updates. When the sequencer resumes, the oracle's last stored answer — which may be hours or days old — is returned as valid, passing all three existing checks because the round is technically "complete" and `answeredInRound == roundID`.

A grep across the entire codebase confirms there is no sequencer uptime feed integration anywhere:

```
No matches found for: sequencer|uptime|L2_SEQUENCER|ArbitrumSequencer
```

This oracle is consumed by L2 pool deposit paths. For example, in `RSETHPool` and `RSETHPoolV3`, the `viewSwapRsETHAmountAndFee` function uses `IOracle(supportedTokenOracle[token]).getRate()` to compute how much wrsETH to mint per unit of collateral: [2](#0-1) 

The rate returned by `ChainlinkOracleForRSETHPoolCollateral` directly determines the rsETH/wrsETH minted per collateral token deposited.

---

### Impact Explanation

If the L2 sequencer was offline during a period when the collateral token's price dropped significantly (e.g., a depeg or market crash), the oracle still returns the pre-downtime (inflated) price when the sequencer resumes. An attacker can immediately deposit collateral at the stale high price and receive more wrsETH than the collateral is actually worth. This results in direct theft of value from the protocol's backing assets — other wrsETH holders' claims are diluted, and the protocol becomes undercollateralized relative to the minted supply.

**Impact: Critical** — Direct theft of user funds / protocol insolvency via oracle manipulation.

---

### Likelihood Explanation

L2 sequencer outages are documented historical events (Arbitrum has experienced multiple outages). The Arbitrum delayed inbox feature additionally allows an attacker to pre-stage a deposit transaction while the sequencer is offline, guaranteeing inclusion at the stale price the moment the sequencer resumes — exactly the attack vector described in the external report. The attacker needs no special privileges; `deposit()` is a public, permissionless function callable by any user.

**Likelihood: Medium** — Requires a sequencer downtime event, but these have occurred historically and the attack is straightforward to execute.

---

### Recommendation

1. Integrate Chainlink's L2 Sequencer Uptime Feed in `ChainlinkOracleForRSETHPoolCollateral.getRate()`. Revert if the sequencer is reported as down.
2. Enforce a minimum grace period (e.g., 1 hour) after sequencer recovery before accepting oracle data, to prevent exploitation of stale prices immediately after resumption.
3. Add a `updatedAt` staleness check (e.g., `block.timestamp - updatedAt > heartbeatInterval`) to reject prices that have not been refreshed within the expected Chainlink heartbeat window.

Example sequencer check pattern:
```solidity
(, int256 answer, uint256 startedAt,,) = sequencerUptimeFeed.latestRoundData();
if (answer != 0) revert SequencerDown();
if (block.timestamp - startedAt < GRACE_PERIOD) revert GracePeriodNotOver();
```

---

### Proof of Concept

1. Attacker monitors the L2 sequencer status. Suppose the sequencer goes offline while collateral token X trades at 1.0 ETH.
2. During downtime, the market price of X drops to 0.7 ETH (e.g., a depeg event).
3. Attacker pre-stages a `deposit(X, largeAmount)` transaction via Arbitrum's delayed inbox.
4. Sequencer resumes. The Chainlink feed for X still returns 1.0 ETH (stale, pre-downtime price). `ChainlinkOracleForRSETHPoolCollateral.getRate()` returns 1.0 ETH — all three existing checks pass.
5. The attacker's deposit is processed: `rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate` uses the stale 1.0 ETH rate instead of the real 0.7 ETH rate. [3](#0-2) 

6. Attacker receives ~43% more wrsETH than their collateral is worth, extracting value from the protocol at the expense of existing wrsETH holders. [1](#0-0)

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

**File:** contracts/pools/RSETHPool.sol (L301-311)
```text

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
    }

    /// @dev view function to get the rsETH amount for a given amount of ETH
    /// @param amount The amount of ETH
    /// @return rsETHAmount The amount of rsETH that will be received
    /// @return fee The fee that will be charged
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
```

**File:** contracts/pools/RSETHPool.sol (L709-722)
```text
    function setSupportedTokenOracle(
        address token,
        address oracle
    )
        external
        onlyRole(TIMELOCK_ROLE)
        onlySupportedToken(token)
    {
        UtilLib.checkNonZeroAddress(oracle);
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
        supportedTokenOracle[token] = oracle;
        emit TokenOracleSet(token, oracle);
```
