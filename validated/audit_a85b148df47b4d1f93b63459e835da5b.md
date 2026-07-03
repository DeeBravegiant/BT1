Now I have all the information needed. Let me analyze the vulnerability carefully.

### Title
Missing Heartbeat Staleness Check Allows Stale Oracle Price Acceptance — (`contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol`)

---

### Summary

`ChainlinkOracleForRSETHPoolCollateral.getRate()` validates Chainlink data with only an `answeredInRound < roundID` guard and a `timestamp == 0` guard. It contains no check that `block.timestamp - updatedAt` is within the feed's heartbeat window. A completed round (`answeredInRound == roundID`, `timestamp != 0`) whose price is arbitrarily old passes all three guards and is returned as valid. Any pool that uses this oracle — including `RSETHPoolV3ExternalBridge` — will mint wrsETH at the stale rate, allowing an attacker to extract excess wrsETH at the expense of the pool's yield reserves.

---

### Finding Description

`getRate()` in `ChainlinkOracleForRSETHPoolCollateral` reads `latestRoundData()` and applies three guards:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol  lines 26-37
function getRate() public view returns (uint256) {
    (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
        AggregatorV3Interface(oracle).latestRoundData();

    if (answeredInRound < roundID) revert StalePrice();   // ← only catches cross-round staleness
    if (timestamp == 0) revert IncompleteRound();          // ← only catches zero timestamp
    if (ethPrice <= 0) revert InvalidPrice();

    uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
    return normalizedPrice;
}
``` [1](#0-0) 

The `answeredInRound < roundID` check only catches the case where Chainlink's aggregator returned an answer that was computed in an earlier round than the current one. It does **not** catch the case where the current round's answer is simply old. When `answeredInRound == roundID` and `timestamp = block.timestamp - 172800` (48 h), all three guards pass and the 48-hour-old price is returned without revert.

The standard industry pattern requires an additional wall-clock staleness check:

```solidity
if (block.timestamp - timestamp > HEARTBEAT_DURATION) revert StalePrice();
```

This check is entirely absent.

---

### Impact Explanation

`RSETHPoolV3ExternalBridge.deposit(token, amount, referralId)` calls `viewSwapRsETHAmountAndFee(amount, token)`, which computes:

```solidity
// contracts/pools/RSETHPoolV3ExternalBridge.sol  lines 442-452
uint256 rsETHToETHrate = getRate();                                          // rsETH oracle
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();     // collateral oracle
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [2](#0-1) 

`ChainlinkOracleForRSETHPoolCollateral` is deployed as the collateral-token oracle (`supportedTokenOracle[token]`). If the feed is stale and the collateral price it reports is **higher** than the current true price (i.e., the collateral has depreciated since the last update), `tokenToETHRate` is inflated, `rsETHAmount` is inflated, and the attacker receives more wrsETH than the deposited collateral is worth. The minted wrsETH is a direct claim on the pool's ETH/collateral reserves; the surplus represents yield that belongs to the protocol being transferred to the attacker.

The `wrsETH.mint(msg.sender, rsETHAmount)` call at line 409 executes unconditionally once the oracle returns: [3](#0-2) 

There is no secondary validation of the oracle value after `getRate()` returns.

---

### Likelihood Explanation

Chainlink feeds on L2s (Arbitrum, Base, Optimism) have documented heartbeat windows (typically 24 h for LST/ETH feeds). Network congestion, sequencer downtime, or oracle node issues can cause feeds to miss updates for hours. The attacker does not need to cause the staleness — they only need to observe it and act before the feed resumes. This is a passive, permissionless, and locally testable condition.

---

### Recommendation

Add a configurable heartbeat constant and check `updatedAt` against it:

```solidity
uint256 public constant HEARTBEAT = 86400; // 24 h; set per-feed

function getRate() public view returns (uint256) {
    (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
        AggregatorV3Interface(oracle).latestRoundData();

    if (answeredInRound < roundID) revert StalePrice();
    if (timestamp == 0) revert IncompleteRound();
    if (block.timestamp - timestamp > HEARTBEAT) revert StalePrice(); // ← add this
    if (ethPrice <= 0) revert InvalidPrice();

    return uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
}
```

The heartbeat value should match the specific Chainlink feed's documented update frequency.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

import "forge-std/Test.sol";
import "contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol";

contract MockAggregator {
    function decimals() external pure returns (uint8) { return 18; }
    function latestRoundData() external view returns (
        uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound
    ) {
        // Round is complete: answeredInRound == roundId, timestamp != 0
        // But price is 48 hours old
        return (5, 1.1e18, block.timestamp - 172800, block.timestamp - 172800, 5);
    }
}

contract StalenessPoC is Test {
    function test_staleOracleDoesNotRevert() public {
        MockAggregator agg = new MockAggregator();
        ChainlinkOracleForRSETHPoolCollateral oracle =
            new ChainlinkOracleForRSETHPoolCollateral(address(agg));

        // This should revert with StalePrice but does NOT
        uint256 rate = oracle.getRate();

        // rate is returned from a 48-hour-old price — no revert
        assertEq(rate, 1.1e18);

        // In the pool: rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate
        // If tokenToETHRate (collateral) is stale-high and rsETHToETHrate is current,
        // rsETHAmount is inflated → attacker receives excess wrsETH
    }
}
```

The test demonstrates that `getRate()` returns successfully with a 48-hour-old price (`answeredInRound == roundID == 5`, `updatedAt = block.timestamp - 172800`, `timestamp != 0`), bypassing all three guards. Wiring this oracle into `RSETHPoolV3ExternalBridge` as `supportedTokenOracle[token]` and calling `deposit(token, amount, referralId)` will mint excess wrsETH proportional to the price deviation, constituting theft of unclaimed yield from the pool.

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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L403-412)
```text
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L442-453)
```text
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
