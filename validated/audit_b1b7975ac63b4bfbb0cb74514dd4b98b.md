Audit Report

## Title
Stale Cross-Chain rsETH/ETH Rate in `CrossChainRateReceiver` Causes Inflated wrsETH Minting in `RSETHPoolV3` Token Deposits — (File: `contracts/cross-chain/CrossChainRateReceiver.sol`)

## Summary

`CrossChainRateReceiver.getRate()` returns the last LayerZero-delivered `rate` with no staleness check, despite recording `lastUpdated` on every update. `RSETHPoolV3.viewSwapRsETHAmountAndFee(amount, token)` divides a fresh Chainlink token/ETH rate by this potentially stale rsETH/ETH rate. When the stored rate lags the true current rate (rsETH has appreciated since the last LZ message), the division yields an inflated wrsETH amount, and the pool mints more wrsETH than the deposited collateral actually backs, degrading the pool's backing ratio.

## Finding Description

`CrossChainRateReceiver.getRate()` simply returns the stored `rate` with no time-based guard:

```solidity
// contracts/cross-chain/CrossChainRateReceiver.sol L102-105
function getRate() external view returns (uint256) {
    return rate;   // no staleness check
}
```

`lastUpdated` is written in `lzReceive` at L97 but is never read in `getRate()`. [1](#0-0) 

`RSETHPoolV3.viewSwapRsETHAmountAndFee(amount, token)` uses this stale rate as the denominator against a fresh Chainlink rate:

```solidity
// contracts/pools/RSETHPoolV3.sol L327-334
uint256 rsETHToETHrate = getRate();                                      // stale LZ rate
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate(); // fresh Chainlink rate
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [2](#0-1) 

`RSETHPoolV3.getRate()` delegates directly to `IOracle(rsETHOracle).getRate()`, which resolves to `CrossChainRateReceiver.getRate()`. [3](#0-2) 

The deposit function calls `viewSwapRsETHAmountAndFee` and immediately mints the returned amount with no post-computation cap: [4](#0-3) 

By contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` enforces freshness via `answeredInRound < roundID` and `timestamp == 0` guards, a pattern absent from `CrossChainRateReceiver`: [5](#0-4) 

The `limitDailyMint` modifier does compute `rsETHAmount` via `viewSwapRsETHAmountAndFee` and checks it against `dailyMintLimit`, but this does not prevent over-minting — it only caps the total inflated amount per day. The inflated rsETH amount is what gets accumulated into `dailyMintAmount`, so the limit is consumed by inflated values and does not correct the per-deposit over-mint. [6](#0-5) 

## Impact Explanation

rsETH is a yield-bearing token whose ETH value monotonically increases over time. Between LZ rate updates, the stored `rate` is lower than the true on-chain rsETH price. For any deposit of a supported non-ETH token (e.g., wstETH):

```
rsETHAmount_minted = amountAfterFee * tokenToETHRate_fresh / rsETHToETHrate_stale
                   > amountAfterFee * tokenToETHRate_fresh / rsETHToETHrate_true
```

The pool mints more wrsETH than the deposited collateral backs at the true current rate. The wrsETH supply is inflated relative to the pool's actual collateral, meaning existing wrsETH holders are diluted and the pool fails to deliver correctly-backed promised returns. This matches the scoped impact: **Low — contract fails to deliver promised returns, but doesn't lose value**.

## Likelihood Explanation

- LZ rate updates are not continuous; gaps of hours between updates are normal operational behavior.
- rsETH accrues yield continuously, so the stored rate is almost always at least slightly stale.
- Any unprivileged depositor of any supported non-ETH token (wstETH, etc.) during a staleness window triggers this path with no special precondition, privilege, or victim mistake required.
- The condition is persistent and repeatable across every deposit during any staleness window.

## Recommendation

Add a staleness threshold check in `CrossChainRateReceiver.getRate()` that reverts if `block.timestamp - lastUpdated` exceeds an acceptable heartbeat, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
uint256 public constant MAX_RATE_AGE = 24 hours;

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= MAX_RATE_AGE, "Rate too stale");
    return rate;
}
```

Alternatively, enforce the check in `RSETHPoolV3.getRate()` so the pool-level wrapper also guards against stale data regardless of which oracle implementation is wired in.

## Proof of Concept

Foundry fork test outline:

1. Fork the target L2 at a block where `CrossChainRateReceiver.lastUpdated` is at least several hours old.
2. Advance `block.timestamp` by the same interval without triggering a new LZ message (so `rate` remains at the old, lower value).
3. Confirm `ChainlinkOracleForRSETHPoolCollateral.getRate()` returns a fresh wstETH/ETH price (no revert).
4. Confirm `CrossChainRateReceiver.getRate()` returns the stale, lower rsETH/ETH rate.
5. Call `RSETHPoolV3.deposit(wstETH, 1e18, "")` as a normal depositor.
6. Record `rsETHAmount` minted (from the `SwapOccurred` event or direct return value via `viewSwapRsETHAmountAndFee`).
7. Compute the correct amount off-chain: `1e18 * wstETHRate / trueRsETHRate` using the true current rsETH price sourced from L1.
8. Assert `rsETHAmount_minted > rsETHAmount_correct` — the pool over-minted wrsETH relative to the true backing, confirming the vulnerability.

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L95-105)
```text
        rate = _rate;

        lastUpdated = block.timestamp;

        emit RateUpdated(_rate);
    }

    /// @notice Gets the last stored rate in the contract
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L96-125)
```text
    modifier limitDailyMint(uint256 amount, address token) {
        if (block.timestamp < startTimestamp) {
            revert MintBeforeStartTimestamp();
        }

        uint256 rsETHAmount;

        // Calculate the amount of rsETH that will be minted
        if (token == ETH_IDENTIFIER) {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);
        } else {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount, token);
        }

        uint256 currentDay = getCurrentDay();

        // If the current day is greater than the last mint day, reset the daily mint amount
        if (currentDay > lastMintDay) {
            lastMintDay = currentDay;
            dailyMintAmount = 0;
        }

        // Check if the daily mint amount plus the amount to mint is greater than the daily mint limit
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L235-237)
```text
    function getRate() public view returns (uint256) {
        return IOracle(rsETHOracle).getRate();
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
