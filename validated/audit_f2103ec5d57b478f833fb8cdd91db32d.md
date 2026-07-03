The oracle code and deposit flow are now fully traced. Here is the complete analysis:

---

### Title
Missing Time-Based Staleness Check in ChainlinkOracleForRSETHPoolCollateral Allows Stale Price Exploitation — (`contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol`)

### Summary

`ChainlinkOracleForRSETHPoolCollateral.getRate()` only validates round completeness (`answeredInRound < roundID`) but performs no time-based staleness check on `updatedAt`. A Chainlink feed can remain in a valid round (`answeredInRound == roundID`) with a price that is hours or days old if the price deviation threshold has not been crossed. An attacker who deposits a supported token while its oracle price is stale-high receives more wrsETH from the pool's inventory than the token's current ETH value warrants, draining the pool.

### Finding Description

`ChainlinkOracleForRSETHPoolCollateral.getRate()` calls `latestRoundData()` and applies three guards: [1](#0-0) 

```
if (answeredInRound < roundID) revert StalePrice();   // round completeness only
if (timestamp == 0)            revert IncompleteRound();
if (ethPrice <= 0)             revert InvalidPrice();
```

There is no guard of the form `if (block.timestamp - updatedAt > HEARTBEAT) revert StalePrice()`. Chainlink feeds update only when the price moves beyond a deviation threshold (e.g. 0.5 %) **or** a heartbeat elapses (e.g. 24 h). Between those events `answeredInRound == roundID` and `timestamp` is non-zero, so all three checks pass — even if the price is many hours old and the market has moved significantly.

The token-deposit path in `RSETHPool` consumes this rate directly: [2](#0-1) 

```
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

The pool then transfers wrsETH from its own balance to the caller: [3](#0-2) 

If `tokenToETHRate` is inflated relative to the true market price, `rsETHAmount` is proportionally inflated, and the pool transfers more wrsETH than the deposited token is worth.

### Impact Explanation

The pool holds a wrsETH inventory that it exchanges for deposited tokens. A stale-high oracle price causes the pool to over-pay in wrsETH for every deposit during the staleness window. The attacker keeps the excess wrsETH; the pool's inventory is permanently reduced by the difference between the stale and true value. This constitutes **theft of unclaimed yield** (the pool's wrsETH inventory represents accrued protocol value).

### Likelihood Explanation

Chainlink feeds for LST collateral (e.g. wstETH/ETH) on Arbitrum have 24-hour heartbeats and 0.5 % deviation thresholds. During periods of low volatility the price can be many hours old while `answeredInRound == roundID`. A sophisticated attacker monitoring on-chain oracle state can identify the staleness window and execute the deposit atomically. No privileged access is required — `deposit(address,uint256,string)` is a public, permissionless function. [4](#0-3) 

### Recommendation

Add a configurable heartbeat staleness check in `ChainlinkOracleForRSETHPoolCollateral.getRate()`:

```solidity
uint256 public immutable heartbeat; // e.g. 86400 for 24 h

if (block.timestamp - timestamp > heartbeat) revert StalePrice();
```

Set `heartbeat` to slightly above the feed's documented update interval (e.g. `86_400 + 300` seconds for a 24-hour feed).

### Proof of Concept

```solidity
// Local fork test (no mainnet interaction)
// 1. Deploy ChainlinkOracleForRSETHPoolCollateral with a mock Chainlink feed.
// 2. Mock feed returns: roundID=5, answeredInRound=5, updatedAt=block.timestamp - 25 hours, price=2e18 (2x fair).
// 3. Deploy RSETHPool; add the mock token with the above oracle; fund pool with 100 wrsETH.
// 4. Attacker deposits 1 token (fair value = 1 ETH, stale oracle says 2 ETH).
//    Expected fair rsETHAmount ≈ 1e18 / rsETHToETHrate.
//    Actual rsETHAmount ≈ 2e18 / rsETHToETHrate  (2× fair).
// 5. Assert: attacker received ~2× fair wrsETH; pool wrsETH balance decreased by ~2× fair amount.
// All three oracle guards pass (answeredInRound == roundID, timestamp != 0, price > 0).
```

### Citations

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L26-36)
```text
    function getRate() public view returns (uint256) {
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
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
