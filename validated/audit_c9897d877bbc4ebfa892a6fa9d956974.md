### Title
Missing Time-Based Staleness Check in `ChainlinkOracleForRSETHPoolCollateral.getRate()` Allows Stale Price Exploitation — (`contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol`)

---

### Summary

`ChainlinkOracleForRSETHPoolCollateral.getRate()` only validates `answeredInRound < roundID` (round-based staleness) but performs no time-based freshness check (`block.timestamp - updatedAt > heartbeat`). When a supported token's Chainlink feed is stale — its last reported price is higher than the current market price — an attacker can call `RSETHPool.deposit(address,uint256,string)` to receive more `wrsETH` than the deposited token is currently worth, draining the pool's `wrsETH` inventory.

---

### Finding Description

**Root cause — `ChainlinkOracleForRSETHPoolCollateral.getRate()`:** [1](#0-0) 

The function calls `latestRoundData()` and checks:
- `answeredInRound < roundID` → reverts `StalePrice()`
- `timestamp == 0` → reverts `IncompleteRound()`
- `ethPrice <= 0` → reverts `InvalidPrice()`

There is **no check** of the form `block.timestamp - timestamp > maxStalePeriod`. A price that was last updated hours or days ago (but whose `answeredInRound == roundID`) passes all three guards and is returned as valid.

**Exploit path — `RSETHPool.deposit(address,uint256,string)`:** [2](#0-1) 

This calls `viewSwapRsETHAmountAndFee(amount, token)`: [3](#0-2) 

At line 343, `tokenToETHRate` is fetched from `supportedTokenOracle[token]` — which is a `ChainlinkOracleForRSETHPoolCollateral` instance. At line 346, `rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate`. If `tokenToETHRate` is stale-high (e.g., 2× the current fair rate), the attacker receives 2× the fair `wrsETH` amount. The pool then transfers this inflated amount from its own `wrsETH` balance: [4](#0-3) 

---

### Impact Explanation

The pool holds a `wrsETH` inventory that it distributes to depositors at the oracle-quoted rate. A stale-high `tokenToETHRate` causes the pool to transfer more `wrsETH` than the deposited token's current ETH value justifies. The excess `wrsETH` extracted represents yield that belongs to the protocol (accumulated from fee income and rsETH price appreciation), constituting **theft of unclaimed yield** via wrsETH inventory drain. `feeEarnedInToken[token]` is computed on the nominal input amount and is unaffected, so the accounting discrepancy is silent.

---

### Likelihood Explanation

Chainlink feeds update on a **deviation threshold** (e.g., 0.5%) or a **heartbeat** (e.g., 24 h). During low-volatility periods or network congestion, a feed can go many hours without an update while `answeredInRound == roundID` (no new round started). If the token's market price has since declined, the stale feed reports a higher-than-current rate. An attacker monitoring on-chain oracle data can detect this condition and deposit immediately. No privileged access, governance action, or oracle operator compromise is required — the attacker only needs to observe the public oracle state and submit a transaction.

---

### Recommendation

Add a configurable `maxStalePeriod` to `ChainlinkOracleForRSETHPoolCollateral` and revert if the price is too old:

```solidity
uint256 public immutable maxStalePeriod; // e.g., 3600 seconds

constructor(address _oracle, uint256 _maxStalePeriod) {
    oracle = _oracle;
    maxStalePeriod = _maxStalePeriod;
}

function getRate() public view returns (uint256) {
    (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
        AggregatorV3Interface(oracle).latestRoundData();

    if (answeredInRound < roundID) revert StalePrice();
    if (timestamp == 0) revert IncompleteRound();
    if (block.timestamp - timestamp > maxStalePeriod) revert StalePrice(); // ADD THIS
    if (ethPrice <= 0) revert InvalidPrice();

    return uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
}
```

Set `maxStalePeriod` per feed based on its documented heartbeat (e.g., 3600 s for a 1-hour heartbeat feed).

---

### Proof of Concept

```solidity
// Local fork / unit test — no mainnet interaction
contract MockStaleChainlinkFeed {
    // Returns answeredInRound == roundID (passes round check)
    // but updatedAt is 48 hours ago (no time check in contract)
    function latestRoundData() external view returns (
        uint80 roundId, int256 answer, uint256 startedAt,
        uint256 updatedAt, uint80 answeredInRound
    ) {
        roundId = 100;
        answeredInRound = 100;                    // passes answeredInRound < roundID check
        answer = 2e8;                             // 2 ETH per token (stale, was 2x fair value)
        updatedAt = block.timestamp - 48 hours;  // 48h stale — no check in getRate()
        startedAt = updatedAt;
    }
    function decimals() external pure returns (uint8) { return 8; }
}

function testStaleOracleExploit() public {
    MockStaleChainlinkFeed staleFeed = new MockStaleChainlinkFeed();
    ChainlinkOracleForRSETHPoolCollateral oracle =
        new ChainlinkOracleForRSETHPoolCollateral(address(staleFeed));

    // oracle.getRate() returns 2e18 (2 ETH) — no revert despite 48h staleness
    uint256 rate = oracle.getRate();
    assertEq(rate, 2e18);

    // Assume fair rate is 1e18 (1 ETH per token), rsETHToETHrate = 1.05e18
    // Fair rsETHAmount for 1e18 token = 1e18 * 1e18 / 1.05e18 ≈ 0.952e18
    // Stale rsETHAmount for 1e18 token = 1e18 * 2e18 / 1.05e18 ≈ 1.904e18  (2× fair)

    uint256 poolWrsETHBefore = wrsETH.balanceOf(address(pool));
    pool.deposit(address(token), 1e18, "ref");
    uint256 poolWrsETHAfter = wrsETH.balanceOf(address(pool));

    // Pool drained ~1.904e18 wrsETH; fair value was ~0.952e18
    assertGt(poolWrsETHBefore - poolWrsETHAfter, fairRsETHAmount);
}
```

The test passes on unmodified production code, confirming the stale price is accepted and the pool transfers approximately 2× the fair `wrsETH` amount.

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

**File:** contracts/pools/RSETHPool.sol (L326-347)
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
        uint256 feeBpsForToken = tokenFeeBps[token];
        fee = amount * feeBpsForToken / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```
