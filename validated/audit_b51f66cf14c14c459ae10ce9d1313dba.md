The code is fully readable. Here is the analysis:

---

### Title
Missing Time-Based Staleness Check in `ChainlinkOracleForRSETHPoolCollateral.getRate()` Allows Stale Price Exploitation — (`contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol`)

### Summary

`ChainlinkOracleForRSETHPoolCollateral.getRate()` only checks `answeredInRound < roundID` to detect stale prices. It does **not** check `block.timestamp - updatedAt > MAX_STALENESS`. When a supported token's Chainlink feed becomes stale (heartbeat missed, feed deprecated, or token depegs faster than the oracle updates), the stale `tokenToETHRate` is used directly in `RSETHPool.viewSwapRsETHAmountAndFee`, allowing an attacker to receive more `wrsETH` than the deposited token is currently worth.

### Finding Description

`ChainlinkOracleForRSETHPoolCollateral.getRate()` fetches `latestRoundData` and applies three guards: [1](#0-0) 

```solidity
if (answeredInRound < roundID) revert StalePrice();   // only catches unfinished rounds
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The `answeredInRound < roundID` check only catches the case where the aggregator has not yet finalized the current round. It does **not** catch the case where the oracle simply has not been updated for an extended period (e.g., 24 h+), which is the standard Chainlink heartbeat-miss scenario. `timestamp` (i.e., `updatedAt`) is fetched but never compared to `block.timestamp`. [2](#0-1) 

In `RSETHPool.viewSwapRsETHAmountAndFee(uint256,address)`, the stale rate is used directly: [3](#0-2) 

```solidity
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

If `tokenToETHRate` is stale and higher than the current market rate (e.g., a token that was at 1.2 ETH/token but has since depegged to 0.5 ETH/token), the attacker receives `wrsETH` valued at 1.2 ETH per token while only providing 0.5 ETH worth of collateral. [4](#0-3) 

The fee is computed on the nominal input amount, so `feeEarnedInToken[token]` is unaffected — the entire excess `wrsETH` is transferred to the attacker.

### Impact Explanation

The pool's `wrsETH` inventory is drained at a rate proportional to the price discrepancy between the stale oracle rate and the true market rate. Every unit of deposited token extracts `staleRate / trueRate` times the fair `wrsETH` amount. This constitutes direct theft of the pool's assets (wrsETH held as inventory), which maps to **High: Theft of unclaimed yield** (pool inventory / yield-bearing assets) at minimum, and arguably **Critical: Direct theft of funds** depending on how the pool's wrsETH is classified.

### Likelihood Explanation

Chainlink heartbeat misses are a known, non-hypothetical event (network congestion, feed deprecation, sequencer downtime on L2). The pool is deployed on **Arbitrum**, where the L2 sequencer can go offline, causing oracle feeds to stop updating while `answeredInRound == roundID` remains true for the last completed round. This is a well-documented attack surface on L2 deployments. No privileged access is required — any address can call `deposit(address,uint256,string)`. [5](#0-4) 

### Recommendation

Add a time-based staleness check in `ChainlinkOracleForRSETHPoolCollateral.getRate()`:

```solidity
uint256 public constant MAX_STALENESS = 86400; // e.g., 24 hours, tune per feed heartbeat

if (block.timestamp - timestamp > MAX_STALENESS) revert StalePrice();
```

Additionally, for Arbitrum deployments, add a Chainlink L2 sequencer uptime feed check before consuming any oracle price. [2](#0-1) 

### Proof of Concept

```solidity
// Local fork test (no mainnet interaction)
// 1. Deploy mock Chainlink aggregator returning price = 2e18 (2x fair value)
//    with updatedAt = block.timestamp - 48 hours, answeredInRound == roundID
// 2. Deploy ChainlinkOracleForRSETHPoolCollateral pointing to mock
// 3. Register token with this oracle in RSETHPool
// 4. Attacker calls deposit(token, 1e18, "")
// 5. Assert: rsETHAmount received == 2 * fairRsETHAmount
// 6. Assert: pool wrsETH balance decreased by 2 * fairRsETHAmount
// 7. Assert: feeEarnedInToken[token] == 1e18 * feeBps / 10_000 (fee on nominal input, not inflated output)
// => attacker extracted 2x wrsETH for 1x token value; pool inventory drained at 2x rate
```

The mock oracle passes all three existing guards (`answeredInRound == roundID`, `timestamp != 0`, `ethPrice > 0`) while returning a price that is 48 hours stale, demonstrating the missing check is the sole root cause.

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
