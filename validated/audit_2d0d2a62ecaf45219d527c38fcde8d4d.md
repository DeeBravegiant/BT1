Audit Report

## Title
Stale Cross-Chain rsETH/ETH Rate Used for L2 Minting Without Freshness Check — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

## Summary
`CrossChainRateReceiver.getRate()` returns the stored `rate` with no check against `lastUpdated`, meaning any L2 pool contract that uses this oracle will mint rsETH/wrsETH using a potentially arbitrarily stale rate. Because rsETH is a yield-bearing token whose price monotonically increases, a stale (lower) rate in the denominator causes over-minting, diluting the yield of all existing rsETH holders.

## Finding Description
`CrossChainRateReceiver` stores the rate received via LayerZero and exposes it unconditionally:

```solidity
// contracts/cross-chain/CrossChainRateReceiver.sol L13-16
uint256 public rate;
uint256 public lastUpdated;

// L103-105
function getRate() external view returns (uint256) {
    return rate;   // no staleness check
}
```

The rate is only updated when `lzReceive()` is triggered by a cross-chain message from `MultiChainRateProvider.updateRate()`, which is permissionless but requires the caller to pay LayerZero gas fees. There is no on-chain enforcement of any update cadence.

Every L2 pool contract (`RSETHPoolV3`, `RSETHPoolNoWrapper`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPool`, `RSETHPoolV2ExternalBridge`) calls `IOracle(rsETHOracle).getRate()` to compute the mint amount:

```solidity
// RSETHPoolV3.sol L303-307 / RSETHPoolNoWrapper.sol L282-285
uint256 rsETHToETHrate = getRate();
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

A stale, lower rate in the denominator produces a larger `rsETHAmount` for the same ETH input. The team demonstrably knows the staleness-check pattern: `ChainlinkOracleForRSETHPoolCollateral.getRate()` checks `answeredInRound < roundID` and reverts with `StalePrice()`, but no equivalent guard exists in `CrossChainRateReceiver`.

## Impact Explanation
**High — Theft of unclaimed yield.**

When the L2 oracle rate is stale and below the true L1 rsETH price, every depositor receives more rsETH than their ETH warrants. The excess rsETH is unbacked. An attacker who bridges the over-minted rsETH to L1 and redeems at the true rate extracts ETH value that was diluted from all existing rsETH holders' accrued yield. The magnitude scales with deposit size and staleness duration. This is a direct, concrete theft of unclaimed yield from all existing holders, matching the allowed High impact class.

## Likelihood Explanation
`updateRate()` is permissionless but requires the caller to pay LayerZero cross-chain gas fees. There is no on-chain time-bound enforcement. During high gas prices, network congestion, or keeper downtime, the rate can remain stale for hours or days. rsETH yield accrues continuously, so even a few hours of staleness creates a profitable window. No special permissions are required — any L2 depositor can exploit it by simply calling `deposit()` during a staleness window. The attack is repeatable across all deployed L2 pool variants.

## Recommendation
Add a configurable `maxStaleness` parameter to `CrossChainRateReceiver` and revert in `getRate()` if the rate is stale:

```solidity
uint256 public maxStaleness; // e.g., 86400 (24 hours)

error StaleRate();

function getRate() external view returns (uint256) {
    if (block.timestamp - lastUpdated > maxStaleness) revert StaleRate();
    return rate;
}
```

Pair this with an automated keeper that calls `updateRate()` on a regular cadence well within `maxStaleness`. The `maxStaleness` value should be set conservatively (e.g., 24 hours) and be updatable by the owner.

## Proof of Concept

**Setup:** L1 rsETH true price = 1.05 ETH/rsETH. L2 `CrossChainRateReceiver.rate` = 1.03e18 (last updated 48 hours ago). No one has called `updateRate()` since.

**Steps:**
1. Attacker calls `deposit{value: 1050 ether}("")` on `RSETHPoolNoWrapper` (or any L2 pool).
2. `viewSwapRsETHAmountAndFee(1050 ether)` calls `getRate()` → returns stale `1.03e18`.
3. `rsETHAmount = 1050e18 * 1e18 / 1.03e18 ≈ 1019.4e18` rsETH minted.
4. Correct amount at true rate: `1050e18 * 1e18 / 1.05e18 = 1000e18` rsETH.
5. Attacker receives ~19.4 rsETH excess.
6. Attacker bridges rsETH to L1 and redeems at 1.05 ETH/rsETH → extracts ~20.4 ETH of value diluted from existing holders.

**Foundry fork test plan:**
```solidity
function testStaleRateOvermint() public {
    // Fork L2 (e.g., Arbitrum mainnet)
    // Warp time forward 48 hours without calling updateRate()
    vm.warp(block.timestamp + 48 hours);
    // Record attacker rsETH balance before
    uint256 before = rsETH.balanceOf(attacker);
    // Deposit 1050 ETH
    vm.prank(attacker);
    pool.deposit{value: 1050 ether}("");
    uint256 minted = rsETH.balanceOf(attacker) - before;
    // Assert minted > 1000e18 (correct amount at true rate)
    assertGt(minted, 1000e18);
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L13-16)
```text
    uint256 public rate;

    /// @notice Last time rate was updated
    uint256 public lastUpdated;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L102-105)
```text
    /// @notice Gets the last stored rate in the contract
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L277-286)
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

**File:** contracts/pools/RSETHPoolV3.sol (L299-307)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
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

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L108-113)
```text
    function updateRate() external payable nonReentrant {
        uint256 latestRate = getLatestRate();

        rate = latestRate;

        lastUpdated = block.timestamp;
```
