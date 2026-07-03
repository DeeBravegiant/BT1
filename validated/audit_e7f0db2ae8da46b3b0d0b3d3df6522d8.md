### Title
`ChainlinkOracleForRSETHPoolCollateral::getRate` Does Not Check L2 Sequencer Status, Enabling Stale-Price Deposits on L2 Pool Contracts - (File: `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol`)

---

### Summary

`ChainlinkOracleForRSETHPoolCollateral.getRate()` calls Chainlink's `latestRoundData()` on L2-deployed pool contracts without verifying that the L2 sequencer is live. When the sequencer is down, Chainlink feeds on L2 can return the last pre-downtime price while appearing fully valid (i.e., `answeredInRound == roundID`, `timestamp != 0`, `price > 0`). Any user can exploit this window to deposit a collateral token at a stale inflated price and receive more wrsETH than the token is currently worth.

---

### Finding Description

`ChainlinkOracleForRSETHPoolCollateral` is a Chainlink wrapper used as the price oracle for supported collateral tokens in the L2 pool contracts (`RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, and others). [1](#0-0) 

`getRate()` performs three checks:

- `answeredInRound < roundID` → reverts as `StalePrice`
- `timestamp == 0` → reverts as `IncompleteRound`
- `ethPrice <= 0` → reverts as `InvalidPrice`

None of these checks detect L2 sequencer downtime. When the sequencer is offline, Chainlink L2 feeds freeze at the last published price. The round is still "answered" (`answeredInRound == roundID`), `timestamp` is non-zero, and `price` is positive — all three guards pass silently. The returned price is simply the last pre-downtime value, which may be significantly different from the true current market price. [2](#0-1) 

This oracle's `getRate()` is consumed by `RSETHPoolV3.viewSwapRsETHAmountAndFee` (and its variants) to price collateral tokens when computing how much wrsETH to mint: [3](#0-2) 

The pool contracts are deployed on multiple L2 chains (Arbitrum, Optimism, Scroll, Base, etc.) as shown in the README, making the L2 sequencer check directly applicable. [4](#0-3) 

---

### Impact Explanation

**Critical — Direct theft of pool funds / protocol insolvency.**

If a collateral token's market price drops while the L2 sequencer is down, the stale (pre-downtime, higher) price is used to compute `rsETHAmount`:

```
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

A depositor receives wrsETH valued at the stale inflated `tokenToETHRate`. When the sequencer recovers and prices normalize, the depositor holds wrsETH worth more than the collateral they deposited. The pool is left holding devalued collateral, directly harming other users and the protocol's solvency. [5](#0-4) 

---

### Likelihood Explanation

L2 sequencer outages are documented historical events on Arbitrum and Optimism. No special privileges are required — any unprivileged user can call `deposit(token, amount, referralId)` during the sequencer downtime window. The attacker only needs to monitor sequencer status and submit a deposit transaction via the L1 delayed inbox (which remains available during sequencer downtime on Arbitrum/Optimism). Likelihood is **Medium** given that sequencer outages are infrequent but have occurred, and the exploit requires no on-chain permissions. [6](#0-5) 

---

### Recommendation

Add a Chainlink L2 sequencer uptime feed check inside `ChainlinkOracleForRSETHPoolCollateral.getRate()`, following [Chainlink's official example](https://docs.chain.link/data-feeds/l2-sequencer-feeds#example-code):

```solidity
// Add sequencer feed as an immutable in the constructor
AggregatorV3Interface public immutable sequencerUptimeFeed;
uint256 private constant GRACE_PERIOD = 3600; // 1 hour

function getRate() public view returns (uint256) {
    // Check sequencer status
    (, int256 answer, uint256 startedAt,,) = sequencerUptimeFeed.latestRoundData();
    bool isSequencerUp = answer == 0;
    if (!isSequencerUp) revert SequencerDown();
    if (block.timestamp - startedAt <= GRACE_PERIOD) revert GracePeriodNotOver();

    // existing checks ...
    (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
        AggregatorV3Interface(oracle).latestRoundData();
    if (answeredInRound < roundID) revert StalePrice();
    if (timestamp == 0) revert IncompleteRound();
    if (ethPrice <= 0) revert InvalidPrice();
    ...
}
```

Also add a heartbeat/staleness check (e.g., `block.timestamp - timestamp > MAX_STALENESS`) since the current `answeredInRound < roundID` check alone does not catch all staleness scenarios. [1](#0-0) 

---

### Proof of Concept

1. L2 sequencer (e.g., Arbitrum) goes offline. Chainlink's WETH/ETH feed on Arbitrum freezes at the last published price, e.g., `1.001e18` (WETH = 1.001 ETH).
2. WETH's true market price drops to `0.98e18` while the sequencer is down.
3. Attacker submits a `deposit(WETH, 100e18, "")` transaction via the L1 delayed inbox (available even during sequencer downtime).
4. `RSETHPoolV3.deposit` → `viewSwapRsETHAmountAndFee(100e18, WETH)` → `IOracle(supportedTokenOracle[WETH]).getRate()` → `ChainlinkOracleForRSETHPoolCollateral.getRate()`.
5. `getRate()` calls `latestRoundData()`, receives the stale `1.001e18` price. All three guards (`answeredInRound`, `timestamp`, `price`) pass.
6. `rsETHAmount = 100e18 * 1.001e18 / rsETHToETHrate` — attacker receives wrsETH priced at `1.001 ETH` per WETH instead of `0.98 ETH`.
7. Attacker gains ~2.1% excess wrsETH (≈2.1 wrsETH per 100 WETH deposited) at the pool's expense.
8. Sequencer recovers; attacker redeems wrsETH at fair value, extracting value from the pool. [1](#0-0) [6](#0-5)

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

**File:** contracts/pools/RSETHPoolV3.sol (L299-308)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L315-335)
```text
    function viewSwapRsETHAmountAndFee(
        uint256 amount,
        address token
    )
        public
        view
        onlySupportedToken(token)
        returns (uint256 rsETHAmount, uint256 fee)
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```

**File:** README.md (L841-844)
```markdown
| Arbitrum     | 0xe119D214a6efa7d3cF60e6E59481EDe1B0064A6B     |
| Optimism     | 0x68A9EC5b93F04a60c77F486a664f283B2E4E2B72     |
| BSC          | 0x4186BFC76E2E237523CBC30FD220FE055156b41F     |

```
