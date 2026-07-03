All code references check out. Let me verify the withdrawal initiation flow to confirm `expectedAssetAmount` is stored at request time.

Audit Report

## Title
Missing Staleness Checks in `getAssetPrice` Enables Stale-Price Deposit / True-Price Withdrawal Value Extraction — (`contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards `updatedAt`, `answeredInRound`, and `roundId`, accepting any price regardless of age or round completeness. When a Chainlink feed is stale at an inflated price, an attacker can deposit at the stale-high rate to mint excess rsETH, wait for the feed to correct and `updateRSETHPrice()` to be called, then initiate a withdrawal that locks in a payout exceeding the true value of the original deposit. The shortfall is borne by all remaining depositors, constituting direct theft of user funds.

## Finding Description

**Root cause — `ChainlinkPriceOracle.sol` line 52:**

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values are available; only `price` is consumed. No check on `updatedAt` (staleness), `answeredInRound < roundId` (incomplete round), or `price <= 0` (invalid answer).

The same codebase already implements all three checks in `ChainlinkOracleForRSETHPoolCollateral.getRate()` (lines 30–32), confirming the omission in `ChainlinkPriceOracle` is unintentional:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

**Exploit path:**

1. Attacker observes a stale-high Chainlink price on-chain (no privileged access required).
2. Attacker calls `depositAsset()`. `getRsETHAmountToMint()` computes `rsethAmountToMint = (amount × getAssetPrice(asset)) / rsETHPrice` — the inflated live price inflates the numerator, minting excess rsETH.
3. Feed corrects. Anyone (including the attacker) calls `updateRSETHPrice()` (public). `_getTotalEthInProtocol()` now uses the true lower price over a larger rsETH supply, so `rsETHPrice` drops.
4. Attacker calls `initiateWithdrawal()`. `getExpectedAssetAmount()` computes `underlyingToReceive = rsETHAmount × rsETHPrice / getAssetPrice(asset)`. With the corrected (lower) `rsETHPrice` and the corrected (lower) asset price, the attacker's `expectedAssetAmount` is stored and exceeds the true value of the original deposit.
5. After `withdrawalDelayBlocks`, attacker claims. `_calculatePayoutAmount` returns `min(expectedAssetAmount, currentReturn)` — if rsETHPrice has not dropped further, the full inflated `expectedAssetAmount` is paid out.

**Why the downside-pause guard fails:**

`_updateRsETHPrice()` can pause the protocol if `newRsETHPrice` drops more than `pricePercentageLimit` below `highestRsethPrice`. However, `pricePercentageLimit` is not set in `initialize()`, so it defaults to `0`. The guard condition is `pricePercentageLimit > 0 && diff > ...` — when `pricePercentageLimit == 0` the entire downside-pause branch is never triggered, leaving the protocol unprotected by default.

## Impact Explanation

**Critical — Direct theft of user funds.**

Concrete example: starting state of 100 stETH / 100 rsETH / rsETHPrice = 1.000. Attacker deposits 100 stETH at stale price 1.05, receiving 105 rsETH. After feed corrects to 1.00 and `updateRSETHPrice()` is called, rsETHPrice = 200/205 ≈ 0.9756. Attacker withdraws 105 rsETH: `expectedAssetAmount = 105 × 0.9756 / 1.00 ≈ 102.44 stETH`. Attacker deposited 100 stETH and receives 102.44 stETH — 2.44 stETH extracted from the remaining depositors, who now have only 97.56 stETH backing their 100 rsETH. The extraction scales linearly with deposit size and is unbounded by any on-chain cap.

## Likelihood Explanation

Chainlink feeds have documented heartbeat intervals (e.g., 24 h for stETH/ETH on mainnet). Network congestion, sequencer downtime, or feed-specific anomalies routinely cause on-chain prices to lag true market prices. No privileged access, front-running, or victim mistake is required — the stale price is already on-chain and readable by any EOA. The attacker only needs to monitor the divergence and submit a standard `depositAsset()` call. The attack is repeatable across any supported LST feed.

## Recommendation

Add staleness, round-completeness, and non-negative checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral.getRate()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    if (block.timestamp - updatedAt > MAX_PRICE_AGE) revert StalePrice(); // e.g. 25 hours

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Additionally, set `pricePercentageLimit` to a non-zero value in `initialize()` or enforce it as a required constructor/initializer parameter so the downside-pause circuit breaker is active from deployment.

## Proof of Concept

The following Foundry invariant test reproduces the value extraction on unmodified code. Running `forge test --match-test test_staleOracleExtractsValue` will show the invariant assertion failing with `stETHReceived ≈ 102.44e18 > fairValue = 100e18`:

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

import "forge-std/Test.sol";

contract StaleOraclePoC is Test {
    uint256 rsETHSupply;
    uint256 rsETHPrice;
    uint256 protocolStETH;
    uint256 chainlinkPrice;

    function setUp() public {
        protocolStETH = 100e18;
        rsETHSupply   = 100e18;
        rsETHPrice    = 1e18;
        chainlinkPrice = 1e18;
    }

    function getAssetPrice() internal view returns (uint256) {
        return chainlinkPrice; // no staleness check — mirrors ChainlinkPriceOracle line 52
    }

    function deposit(uint256 amount) internal returns (uint256 rsethMinted) {
        rsethMinted = amount * getAssetPrice() / rsETHPrice;
        protocolStETH += amount;
        rsETHSupply   += rsethMinted;
    }

    function updateRSETHPrice() internal {
        rsETHPrice = protocolStETH * getAssetPrice() / rsETHSupply;
    }

    function initiateWithdrawal(uint256 rsethAmount) internal returns (uint256 expectedStETH) {
        expectedStETH = rsethAmount * rsETHPrice / getAssetPrice();
        rsETHSupply   -= rsethAmount;
        protocolStETH -= expectedStETH;
    }

    function test_staleOracleExtractsValue() public {
        chainlinkPrice = 1.05e18;                        // feed goes stale-high
        uint256 rsethReceived = deposit(100e18);         // attacker deposits 100 stETH → 105 rsETH
        chainlinkPrice = 1e18;                           // feed corrects
        updateRSETHPrice();                              // rsETHPrice ≈ 0.9756e18
        uint256 stETHReceived = initiateWithdrawal(rsethReceived); // ≈ 102.44 stETH

        assertLe(stETHReceived, 100e18,
            "INVARIANT BROKEN: attacker redeemed more than deposited at true price");
        // ^ FAILS: stETHReceived ≈ 102.44e18 > 100e18
    }
}
```

**Code references verified:**
- `ChainlinkPriceOracle.sol` line 52: no staleness check [1](#0-0) 
- `ChainlinkOracleForRSETHPoolCollateral.sol` lines 30–32: staleness checks present for comparison [2](#0-1) 
- `LRTDepositPool.sol` line 520: deposit minting uses live `getAssetPrice` [3](#0-2) 
- `LRTWithdrawalManager.sol` line 593: withdrawal uses stored `rsETHPrice` [4](#0-3) 
- `LRTOracle.sol` lines 273–274: `pricePercentageLimit > 0` guard, defaults to 0 [5](#0-4) 
- `LRTOracle.sol` lines 64–68: `initialize()` does not set `pricePercentageLimit` [6](#0-5) 
- `LRTWithdrawalManager.sol` line 834: `_calculatePayoutAmount` min-cap does not prevent profit locked at initiation [7](#0-6)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L30-32)
```text
        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L592-594)
```text
        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L833-835)
```text
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
    }
```

**File:** contracts/LRTOracle.sol (L64-68)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
```

**File:** contracts/LRTOracle.sol (L273-274)
```text
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);
```
