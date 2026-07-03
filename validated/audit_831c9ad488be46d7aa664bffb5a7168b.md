### Title
Missing Sequencer Uptime Feed Check Enables Stale-Price Overminting on Arbitrum — (`contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol`)

---

### Summary

`ChainlinkOracleForRSETHPoolCollateral.getRate()` calls Chainlink's `latestRoundData()` on Arbitrum without verifying the sequencer uptime feed. When the Arbitrum sequencer recovers from downtime, all buffered price updates become available simultaneously. A user who deposits a supported token (e.g., wstETH) in that window receives rsETH calculated against a stale, inflated token/ETH rate, extracting more rsETH than the actual value of their deposit.

---

### Finding Description

`ChainlinkOracleForRSETHPoolCollateral.getRate()` fetches the token/ETH price directly from a Chainlink aggregator:

```solidity
(uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
    AggregatorV3Interface(oracle).latestRoundData();
```

It validates staleness (`answeredInRound < roundID`), round completeness (`timestamp == 0`), and sign (`ethPrice <= 0`), but it performs **no check against the Arbitrum Sequencer Uptime Feed**. [1](#0-0) 

This oracle is wired into `RSETHPoolV3` as `supportedTokenOracle[token]` and is called inside `viewSwapRsETHAmountAndFee`:

```solidity
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [2](#0-1) 

The same pattern exists in `RSETHPoolNoWrapper`: [3](#0-2) 

When the Arbitrum sequencer is down, Chainlink cannot push new price rounds. When it recovers, all pending updates land at once. In the brief window before the on-chain price catches up to the true market price, a depositor can submit a token deposit and receive rsETH calculated at the pre-downtime (stale, inflated) token/ETH rate.

`RSETHPriceFeed.sol` has the same gap — it calls `ETH_TO_USD.latestRoundData()` without a sequencer check, meaning the rsETH/USD composite price it exposes to external consumers (e.g., lending protocols on Arbitrum) is also unreliable during sequencer recovery: [4](#0-3) 

---

### Impact Explanation

**High — Theft of unclaimed yield / protocol insolvency risk.**

The rsETH minting formula `rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate` means that an inflated `tokenToETHRate` directly inflates the rsETH minted per unit of deposited token. The attacker receives rsETH backed by less real value than the protocol expects, diluting all existing rsETH holders. If the price deviation is large (sequencer outages of 30+ minutes have occurred on Arbitrum) and the daily mint limit has not yet been hit, the overminting can be material.

---

### Likelihood Explanation

**Medium.** Arbitrum sequencer outages are a documented, recurring event (multiple incidents in 2022–2023). The attack requires no special privilege — any address can call `deposit(token, amount, referralId)` on `RSETHPoolV3` or `RSETHPoolNoWrapper`. The attacker only needs to monitor the sequencer status and submit a deposit transaction immediately after recovery, before Chainlink prices update on-chain.

---

### Recommendation

Follow the Chainlink documentation for L2 sequencer uptime feeds. Add a sequencer check and a grace period (e.g., 1 hour) inside `ChainlinkOracleForRSETHPoolCollateral.getRate()` and `RSETHPriceFeed.latestRoundData()`:

```solidity
// Example pattern (Chainlink docs: https://docs.chain.link/data-feeds/l2-sequencer-feeds)
(, int256 answer, uint256 startedAt,,) = sequencerUptimeFeed.latestRoundData();
if (answer != 0) revert SequencerDown();
if (block.timestamp - startedAt < GRACE_PERIOD) revert GracePeriodNotOver();
```

The `GRACE_PERIOD` should be long enough for users to react (Chainlink recommends ≥ 1 hour).

---

### Proof of Concept

1. Arbitrum sequencer goes offline. wstETH/ETH price on mainnet drops from 1.15 ETH to 1.05 ETH.
2. Sequencer recovers. Chainlink on Arbitrum still reports 1.15 ETH (stale).
3. Attacker calls `RSETHPoolV3.deposit(wstETH, 100e18, "")`.
4. `viewSwapRsETHAmountAndFee` computes `tokenToETHRate = 1.15e18` (stale) and `rsETHToETHrate = 1.08e18` (cross-chain rate, correct).
5. Attacker receives `100 * 1.15 / 1.08 ≈ 106.48` rsETH instead of the correct `100 * 1.05 / 1.08 ≈ 97.22` rsETH — a ~9.5% overmint at the expense of existing rsETH holders.
6. Attacker bridges or sells the excess rsETH before the price corrects.

Entry path: `deposit(token, amount, referralId)` → `viewSwapRsETHAmountAndFee(amount, token)` → `IOracle(supportedTokenOracle[token]).getRate()` → `ChainlinkOracleForRSETHPoolCollateral.getRate()` → stale `latestRoundData()`. [5](#0-4) [1](#0-0)

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

**File:** contracts/pools/RSETHPoolV3.sol (L271-293)
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
        limitDailyMint(amount, token)
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L331-334)
```text
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L308-311)
```text
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/oracles/RSETHPriceFeed.sol (L63-70)
```text
    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
        answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
    }
```
