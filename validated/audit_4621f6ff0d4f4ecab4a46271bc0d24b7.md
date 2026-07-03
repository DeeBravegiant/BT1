All code references have been verified against the actual source. The finding is confirmed valid.

Audit Report

## Title
Zero-initialized `rate` in `CrossChainRateReceiver` causes division-by-zero in pool deposits during bootstrap window — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

## Summary
`CrossChainRateReceiver` zero-initializes `uint256 public rate` and `RSETHRateReceiver`'s constructor never sets it, leaving `rate == 0` until the first `lzReceive` call. Both `RSETHPoolV2` and `RSETHPoolV3` divide by this value in `viewSwapRsETHAmountAndFee` with no zero-guard, causing every `deposit` call to panic-revert during the window between deployment and first cross-chain rate delivery.

## Finding Description
`CrossChainRateReceiver` declares `uint256 public rate` at line 13, which Solidity zero-initializes. [1](#0-0) 

`RSETHRateReceiver`'s constructor sets `srcChainId`, `rateProvider`, `layerZeroEndpoint`, and `rateInfo`, but never assigns `rate`, so it remains `0` until `lzReceive` is called. [2](#0-1) 

`getRate()` returns `rate` with no zero-check: [3](#0-2) 

In `RSETHPoolV2.viewSwapRsETHAmountAndFee`, the pool fetches this rate and immediately divides by it with no guard: [4](#0-3) 

The same unguarded division exists in `RSETHPoolV3.viewSwapRsETHAmountAndFee` (ETH path) and the token path: [5](#0-4) [6](#0-5) 

`deposit` in both pools applies the `limitDailyMint` modifier, which calls `viewSwapRsETHAmountAndFee` before any other logic, so the division-by-zero panic fires on every deposit attempt: [7](#0-6) 

The only existing zero-rate guard (`if (rsETHToETHrate == 0) revert UnsupportedOracle()`) is confined to `viewSwapAssetToPremintedRsETH` and does not protect the deposit path: [8](#0-7) 

## Impact Explanation
Every call to `deposit` reverts with a Solidity division-by-zero panic for the entire period between pool deployment and the first successful LayerZero `lzReceive` delivery. No user can deposit ETH (or supported tokens in V3) during this window. ETH sent with the call is returned by the EVM revert, so funds are not permanently lost, but the pool is completely non-functional. This matches **Medium: Temporary freezing of funds**.

## Likelihood Explanation
LayerZero cross-chain message delivery is not atomic with deployment; it requires at least one block confirmation on the source chain plus relayer processing time (typically minutes, potentially longer under congestion). The `reinitialize` function enforces `startTimestamp` to be in the future, but provides no on-chain guarantee that `lzReceive` has been called before `startTimestamp` is reached. Any user who attempts a deposit in this window — which is publicly visible on-chain — will have their transaction revert. No attacker is required; this is a structural property of the deployment sequence.

## Recommendation
Add a zero-rate guard in `viewSwapRsETHAmountAndFee` (both V2 and V3) before the division:

```solidity
uint256 rsETHToETHrate = getRate();
if (rsETHToETHrate == 0) revert InvalidRate();
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

Alternatively, validate in `CrossChainRateReceiver.getRate()` itself:

```solidity
function getRate() external view returns (uint256) {
    require(rate != 0, "Rate not initialized");
    return rate;
}
```

A complementary deployment practice is to enforce on-chain that `rate != 0` before the pool is opened to users, e.g., by requiring `lastUpdated != 0` as a precondition in `reinitialize` or via a separate activation step.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

function test_depositRevertsWhenRateIsZero() public {
    // 1. Deploy RSETHRateReceiver — rate == 0, no lzReceive called
    RSETHRateReceiver receiver = new RSETHRateReceiver(srcChainId, rateProvider, lzEndpoint);
    assertEq(receiver.getRate(), 0);

    // 2. Deploy and initialize RSETHPoolV2 with receiver as rsETHOracle
    // 3. Call reinitialize with startTimestamp = block.timestamp (present or past)
    //    Note: reinitialize enforces startTimestamp >= block.timestamp, so set it to block.timestamp

    // 4. User calls deposit — limitDailyMint modifier fires viewSwapRsETHAmountAndFee
    //    → rsETHToETHrate = IOracle(rsETHOracle).getRate() == 0
    //    → rsETHAmount = (amount - fee) * 1e18 / 0  ← PANIC: division by zero
    vm.expectRevert();
    pool.deposit{value: 1 ether}("ref");
}
```

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L13-13)
```text
    uint256 public rate;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L103-105)
```text
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/cross-chain/RSETHRateReceiver.sol (L10-15)
```text
    constructor(uint16 _srcChainId, address _rateProvider, address _layerZeroEndpoint) {
        rateInfo = RateInfo({ tokenSymbol: "rsETH", baseTokenSymbol: "ETH" });
        srcChainId = _srcChainId;
        rateProvider = _rateProvider;
        layerZeroEndpoint = _layerZeroEndpoint;
    }
```

**File:** contracts/pools/RSETHPoolV2.sol (L72-78)
```text
    modifier limitDailyMint(uint256 amount) {
        if (block.timestamp < startTimestamp) {
            revert MintBeforeStartTimestamp();
        }

        // Calculate the amount of rsETH that will be minted
        (uint256 rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);
```

**File:** contracts/pools/RSETHPoolV2.sol (L230-233)
```text
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3.sol (L303-307)
```text
        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3.sol (L328-334)
```text
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3.sol (L392-393)
```text
        uint256 rsETHToETHrate = getRate();
        if (rsETHToETHrate == 0) revert UnsupportedOracle();
```
