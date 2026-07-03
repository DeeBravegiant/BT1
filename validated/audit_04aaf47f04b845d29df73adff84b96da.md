Audit Report

## Title
Missing L2 Sequencer Uptime Check in Chainlink Oracle Allows Stale Price Exploitation - (File: contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol)

## Summary
`ChainlinkOracleForRSETHPoolCollateral.getRate()` queries a Chainlink price feed on L2 without checking the Chainlink L2 sequencer uptime feed. During Arbitrum sequencer downtime, the oracle cannot receive new rounds, so it returns the last pre-downtime price. All three existing guards pass on a stale price, and an attacker can force-include a deposit transaction via the Arbitrum L1 delayed inbox to receive rsETH at an inflated stale rate, extracting value from the pool.

## Finding Description
`ChainlinkOracleForRSETHPoolCollateral.getRate()` calls `AggregatorV3Interface(oracle).latestRoundData()` and applies three guards:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [1](#0-0) 

None of these guards check the Chainlink L2 sequencer uptime feed. When the Arbitrum sequencer is down, Chainlink oracle nodes cannot push new rounds to the L2 feed. As a result: `roundID` and `answeredInRound` remain equal (no new round is pushed), so `answeredInRound < roundID` evaluates to `false` — `StalePrice` is never triggered. `timestamp` is non-zero (it is the timestamp of the last pre-downtime update), so `IncompleteRound` is not triggered. `ethPrice > 0`, so `InvalidPrice` is not triggered. The stale pre-downtime price is returned without any indication of staleness.

There is also no heartbeat/time-elapsed check (`block.timestamp - timestamp > HEARTBEAT`), compounding the staleness window.

This oracle is consumed by `RSETHPool.deposit(address token, uint256 amount, string referralId)` and `RSETHPoolNoWrapper.deposit(address token, uint256 amount, string referralId)`, both of which are public, permissionless, and unguarded by any daily mint limit: [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

The Arbitrum force-inclusion mechanism (`IInbox.createRetryableTicket`) is a documented, permissionless L1 call that allows transactions to bypass the sequencer after the sequencer delay (~24 hours on Arbitrum One). An attacker can submit a deposit to the L1 delayed inbox at the start of a sequencer outage, wait for the delay to expire, call `forceInclusion()`, and have the deposit execute on L2 against the stale oracle price.

## Impact Explanation
The pool transfers rsETH to the depositor based on `tokenToETHRate / rsETHToETHrate`, where `tokenToETHRate` is the stale inflated collateral price from `ChainlinkOracleForRSETHPoolCollateral.getRate()`. If the collateral token dropped in value while the sequencer was down, the attacker receives more rsETH than the deposited collateral is worth. This is direct theft of funds from the pool at the expense of other depositors and the protocol.

**Impact: Critical** — direct theft of user funds from the pool.

## Likelihood Explanation
Arbitrum sequencer outages have occurred historically (December 2021, January 2022, June 2023). The force-inclusion mechanism is permissionless and documented. The primary constraint is the ~24-hour sequencer delay on Arbitrum One, meaning the sequencer must remain down for at least that duration for the attack to execute. Extended outages have occurred. No privileged access is required; any external user can call `IInbox.createRetryableTicket` on L1 and subsequently `forceInclusion()`.

**Likelihood: Medium** — requires sequencer downtime exceeding the force-inclusion delay and observable price divergence, but no admin compromise or special access.

## Recommendation
Integrate the Chainlink L2 sequencer uptime feed into `getRate()` following Chainlink's documented pattern. Add a `sequencerUptimeFeed` immutable address to `ChainlinkOracleForRSETHPoolCollateral` and perform the check at the top of `getRate()` before calling `latestRoundData()` on the price feed:

```solidity
(, int256 answer, uint256 startedAt,,) = sequencerUptimeFeed.latestRoundData();
if (answer == 1) revert SequencerDown();
if (block.timestamp - startedAt < GRACE_PERIOD) revert GracePeriodNotOver();
```

Additionally, add a heartbeat staleness check: `if (block.timestamp - timestamp > HEARTBEAT) revert StalePrice();`

## Proof of Concept
1. The Arbitrum sequencer goes offline. The collateral token (e.g., wstETH) drops 15% in market value while the sequencer is down.
2. The Chainlink wstETH/ETH feed on Arbitrum cannot be updated — it still reports the pre-downtime price.
3. Attacker calls `IInbox.createRetryableTicket` on Ethereum L1 targeting `RSETHPool.deposit(wstETH, amount, "")` on Arbitrum.
4. After the sequencer delay (~24 hours), attacker calls `forceInclusion()` on the Arbitrum SequencerInbox.
5. The deposit executes on L2: `RSETHPool` calls `IOracle(supportedTokenOracle[wstETH]).getRate()` → `ChainlinkOracleForRSETHPoolCollateral.getRate()`.
6. `latestRoundData()` returns the stale pre-downtime price. `answeredInRound == roundID` (no new round was pushed), so `StalePrice` is not triggered. `timestamp != 0` and `ethPrice > 0`, so all guards pass.
7. The pool transfers rsETH based on the inflated stale price. The attacker receives ~15% more rsETH than the deposited collateral is worth.

**Foundry fork test plan:** Fork Arbitrum mainnet at a block just before a historical sequencer outage. Mock the Chainlink wstETH/ETH feed to return a stale price (simulate no new rounds). Call `RSETHPool.deposit(wstETH, amount, "")` directly (simulating force-inclusion execution context). Assert that `rsETHAmount` received exceeds the fair value of the deposited collateral at the true market price.

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

**File:** contracts/pools/RSETHPool.sol (L340-347)
```text
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L250-271)
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

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L305-312)
```text
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```
