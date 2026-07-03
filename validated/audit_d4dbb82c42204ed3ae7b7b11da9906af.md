### Title
Stale Cross-Chain rsETH/ETH Rate in `CrossChainRateReceiver` Causes Inflated wrsETH Minting in `RSETHPoolV3` Token Deposits — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

---

### Summary

`CrossChainRateReceiver.getRate()` returns the last stored LZ-delivered rate with no staleness check. `RSETHPoolV3.viewSwapRsETHAmountAndFee(amount, token)` divides a fresh Chainlink token/ETH rate by this potentially stale rsETH/ETH rate. When the stale rate is lower than the true current rate (rsETH has appreciated since the last LZ message), the division yields an inflated wrsETH amount, and the pool mints more wrsETH than the deposited collateral actually backs.

---

### Finding Description

**Rate staleness asymmetry in `viewSwapRsETHAmountAndFee`:**

`RSETHPoolV3.viewSwapRsETHAmountAndFee(amount, token)` fetches two rates:

```solidity
// contracts/pools/RSETHPoolV3.sol lines 327-334
uint256 rsETHToETHrate = getRate();                                          // ← stale LZ rate
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();     // ← fresh Chainlink rate
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [1](#0-0) 

`getRate()` on the pool delegates to `IOracle(rsETHOracle).getRate()`, which resolves to `CrossChainRateReceiver.getRate()`:

```solidity
// contracts/cross-chain/CrossChainRateReceiver.sol lines 102-105
function getRate() external view returns (uint256) {
    return rate;   // ← raw stored value, no staleness check
}
``` [2](#0-1) 

`lastUpdated` is recorded in `lzReceive` but is never consulted in `getRate()`: [3](#0-2) 

By contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` enforces freshness via `answeredInRound < roundID` and `timestamp == 0` guards: [4](#0-3) 

**Deposit path:**

`RSETHPoolV3.deposit(token, amount, referralId)` calls `viewSwapRsETHAmountAndFee` and immediately mints the returned amount: [5](#0-4) 

There is no post-computation guard that caps or validates the minted amount against the true current rsETH rate.

---

### Impact Explanation

rsETH is a yield-bearing token whose ETH value monotonically increases over time. Between LZ rate updates, the stored `rate` in `CrossChainRateReceiver` is lower than the true on-chain rsETH price. When a depositor deposits wstETH (or any supported non-ETH token):

```
rsETHAmount = amountAfterFee * tokenToETHRate_fresh / rsETHToETHrate_stale
```

Because `rsETHToETHrate_stale < rsETHToETHrate_true`, the result is:

```
rsETHAmount_minted > rsETHAmount_correct
```

The pool mints more wrsETH than the deposited collateral backs at the true current rate. The wrsETH supply is inflated relative to the pool's actual collateral, meaning the pool fails to deliver correctly-backed promised returns. This matches the scoped impact: **Low — contract fails to deliver promised returns, but doesn't lose value** (the depositor receives excess wrsETH; the pool's backing ratio is degraded).

---

### Likelihood Explanation

- LZ rate updates are not continuous; they are triggered manually or on a schedule. Gaps of hours are normal.
- rsETH accrues yield continuously, so the stored rate is almost always slightly stale.
- Any depositor of a supported non-ETH token (wstETH, etc.) during a staleness window triggers this path without any special precondition or privilege.
- The `limitDailyMint` modifier is keyed on the raw token `amount`, not the minted rsETH amount, so it does not prevent over-minting. [6](#0-5) 

---

### Recommendation

Add a staleness threshold check in `CrossChainRateReceiver.getRate()` (or in the pool's `getRate()` wrapper) that reverts if `block.timestamp - lastUpdated` exceeds an acceptable heartbeat (e.g., 24 hours). This mirrors the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`.

```solidity
function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= MAX_RATE_AGE, "Rate too stale");
    return rate;
}
```

---

### Proof of Concept

Fork test outline (local fork, no mainnet execution):

1. Fork the target L2 at a block where `RSETHRateReceiver.lastUpdated` is, say, 12 hours old.
2. Advance `block.timestamp` by 12 hours without triggering a new LZ message (so `rate` remains at the old, lower value).
3. Confirm `ChainlinkOracleForRSETHPoolCollateral.getRate()` returns a fresh wstETH/ETH price.
4. Call `RSETHPoolV3.deposit(wstETH, 1e18, "")` as a normal depositor.
5. Record `rsETHAmount` minted.
6. Compute the correct amount: `1e18 * wstETHRate / trueRsETHRate` using the true current rsETH price from L1.
7. Assert `rsETHAmount_minted > rsETHAmount_correct` — the pool over-minted wrsETH relative to the true backing.

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L96-100)
```text
    modifier limitDailyMint(uint256 amount, address token) {
        if (block.timestamp < startTimestamp) {
            revert MintBeforeStartTimestamp();
        }

```

**File:** contracts/pools/RSETHPoolV3.sol (L284-292)
```text
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
```

**File:** contracts/pools/RSETHPoolV3.sol (L327-334)
```text
        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L95-97)
```text
        rate = _rate;

        lastUpdated = block.timestamp;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L102-105)
```text
    /// @notice Gets the last stored rate in the contract
    function getRate() external view returns (uint256) {
        return rate;
    }
```

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
