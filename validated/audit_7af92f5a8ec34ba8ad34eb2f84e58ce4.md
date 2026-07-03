Audit Report

## Title
Missing Zero-Rate Validation in `lzReceive` Enables Temporary Freezing of All Pool Deposits — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

## Summary

`CrossChainRateReceiver.lzReceive` writes any decoded `_rate` value, including zero, directly to storage without validation. Because `RSETHPoolV2.viewSwapRsETHAmountAndFee` and both overloads of `RSETHPoolV3.viewSwapRsETHAmountAndFee` divide by `rsETHToETHrate` with no zero-guard, a single authenticated cross-chain message carrying `_rate=0` causes every subsequent `deposit` call to revert with a Solidity division-by-zero panic, freezing all pool deposits until the next valid rate update arrives.

## Finding Description

**Root cause — `CrossChainRateReceiver.lzReceive` (lines 93–97):**

The function correctly authenticates the message (endpoint, source chain, source address) but performs no sanity check on the decoded rate before persisting it:

```solidity
uint256 _rate = abi.decode(_payload, (uint256));
rate = _rate;          // ← no require(_rate > 0)
lastUpdated = block.timestamp;
``` [1](#0-0) 

**Propagation — `RSETHPoolV2.viewSwapRsETHAmountAndFee` (line 233):**

```solidity
uint256 rsETHToETHrate = getRate();
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;  // panic if rate == 0
``` [2](#0-1) 

**Same pattern in `RSETHPoolV3.viewSwapRsETHAmountAndFee` (ETH path, line 307):** [3](#0-2) 

**And in `RSETHPoolV3.viewSwapRsETHAmountAndFee` (token path, line 334):** [4](#0-3) 

**`deposit` is fully blocked:** the `limitDailyMint` modifier in both V2 and V3 calls `viewSwapRsETHAmountAndFee` *before* the function body executes, so the division-by-zero revert fires before any state change: [5](#0-4) [6](#0-5) 

**Inconsistency confirming developer awareness:** `RSETHPoolV3.viewSwapAssetToPremintedRsETH` *does* guard against zero:

```solidity
uint256 rsETHToETHrate = getRate();
if (rsETHToETHrate == 0) revert UnsupportedOracle();
``` [7](#0-6) 

This guard is absent in both `viewSwapRsETHAmountAndFee` overloads.

## Impact Explanation

Once `rate` is set to zero, every call to `RSETHPoolV2.deposit` and `RSETHPoolV3.deposit` (both ETH and ERC-20 paths) reverts with a division-by-zero panic. No user can deposit until the next valid cross-chain rate update overwrites the zero. This constitutes **temporary freezing of funds** (Medium severity per the allowed impact scope).

## Likelihood Explanation

The trigger requires a legitimately authenticated LayerZero message (correct endpoint, correct source chain, correct `rateProvider` address) carrying `_rate=0`. This can arise from a transient oracle fault on the provider chain (e.g., the underlying price feed returning 0 during an upgrade or edge-case initialization state) or a bug in `CrossChainRateProvider` that sends 0 before the oracle is fully initialized. No admin compromise, key leak, or malicious actor is required — the message passes all three authentication checks in `lzReceive`, and the missing guard is the sole root cause.

## Recommendation

Add a zero-value guard in `lzReceive` before writing to storage:

```solidity
require(_rate > 0, "Rate must be non-zero");
rate = _rate;
``` [8](#0-7) 

Additionally, add matching guards in both `viewSwapRsETHAmountAndFee` functions (V2 and V3) consistent with the existing guard in `viewSwapAssetToPremintedRsETH`.

## Proof of Concept

1. Deploy `CrossChainRateReceiver` with a configured `layerZeroEndpoint`, `rateProvider`, and `srcChainId`.
2. Deploy `RSETHPoolV2` (or V3) pointing to the receiver as its oracle.
3. Call `lzReceive` from the configured `layerZeroEndpoint` with `_srcChainId == srcChainId`, `_srcAddress == abi.encodePacked(rateProvider)`, and `_payload == abi.encode(uint256(0))`.
4. Verify `receiver.getRate() == 0`.
5. Attempt `pool.deposit{value: 1 ether}("ref")` — the call reverts with a Solidity division-by-zero panic due to `amountAfterFee * 1e18 / rsETHToETHrate` where `rsETHToETHrate == 0`.
6. The freeze persists until a subsequent valid `lzReceive` call with `_rate > 0` overwrites the stored rate.

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L93-97)
```text
        uint256 _rate = abi.decode(_payload, (uint256));

        rate = _rate;

        lastUpdated = block.timestamp;
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

**File:** contracts/pools/RSETHPoolV3.sol (L96-108)
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
