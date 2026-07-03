Audit Report

## Title
Protocol Fee Charged on Gross Recovery Instead of Net Gain Above High-Water Mark — (`contracts/LRTOracle.sol`)

## Summary

`_updateRsETHPrice()` reconstructs `previousTVL` each call as `rsethSupply × rsETHPrice`, where `rsETHPrice` is the post-fee, post-loss stored price. After a loss, `rsETHPrice` falls, lowering `previousTVL` and creating a new, artificially low baseline. When the TVL recovers, the fee is charged on the full gross recovery rather than only the net gain above the prior fee-bearing level, causing the treasury to extract rsETH that economically belongs to depositors.

## Finding Description

In `_updateRsETHPrice()` at [1](#0-0) , `previousTVL` is computed as `rsethSupply.mulWad(rsETHPrice)`. The stored `rsETHPrice` reflects both prior fee deductions and any subsequent losses. When a loss occurs (step 3 in the PoC), no fee is taken, but `rsETHPrice` is written down to the loss-adjusted price at [2](#0-1) . On the next update after recovery, `previousTVL` is computed from this lower price, so the fee base (`rewardAmount = totalETHInProtocol - previousTVL`) includes the entire recovery — not just the marginal gain above the prior fee-bearing TVL.

The fee logic at [3](#0-2)  has no high-water mark: it only checks `totalETHInProtocol > previousTVL`, where `previousTVL` is reset downward by every loss. The downside-protection pause at [4](#0-3)  only fires when `pricePercentageLimit > 0` and the drop exceeds the configured threshold; if `pricePercentageLimit == 0` (the default unset state) or the loss is below the threshold, no pause occurs and the scenario proceeds unimpeded. Even when a pause does occur, the admin unpausing is a normal operational action, after which the same over-fee is charged on recovery.

Concrete trace (10% fee = 1000 BPS):

| Step | `totalETHInProtocol` | `rsETHPrice` stored | `previousTVL` | Fee charged |
|------|---------------------|---------------------|---------------|-------------|
| 1 | 1000 | 1.000 | — | — |
| 2 | 1100 | 1.090 | 1000 | 10 ETH (correct) |
| 3 | 1020 | 1.020 | 1090 | 0 |
| 4 | 1100 | 1.092 | 1020 | **8 ETH** (correct: 1 ETH) |

The treasury extracts 7 ETH of value that belongs to depositors.

## Impact Explanation

This is **theft of unclaimed yield** (High). The treasury is minted rsETH backed by ETH that represents a recovery of depositor principal/yield, not new protocol earnings. Every rsETH holder is diluted proportionally. The magnitude scales with the loss size and `protocolFeeInBPS` (up to 1500 BPS per [5](#0-4) ). The impact is concrete and directly traceable to the accounting logic in this repository.

## Likelihood Explanation

`updateRSETHPrice()` is public with no access control beyond `whenNotPaused` at [6](#0-5) . EigenLayer restaking strategies are subject to slashing and market fluctuations, making partial loss-recovery cycles realistic and recurring. When `pricePercentageLimit == 0` or the loss is below the pause threshold, any external caller can trigger the over-fee by calling `updateRSETHPrice()` after a recovery. No privileged access, front-running, or victim mistake is required.

## Recommendation

Introduce a `feeAdjustedHighWaterMark` storage variable that is set to `totalETHInProtocol` after each fee-bearing update and is **never reduced** on a loss. Replace the `previousTVL` computation with `rsethSupply.mulWad(rsETHPrice)` only for the no-fee path; for the fee path, use `feeAdjustedHighWaterMark` as the baseline so that fees are only charged on TVL increases above the last fee-bearing level. Alternatively, store `previousTVLAfterFee` explicitly at the end of each fee-bearing update and use it directly as the baseline in the next call.

## Proof of Concept

Foundry fork/unit test plan:

1. Deploy `LRTOracle` with `protocolFeeInBPS = 1000` (10%) and `pricePercentageLimit = 0`.
2. Set `rsethSupply = 1000e18`, `rsETHPrice = 1e18`, `totalETHInProtocol = 1000e18`.
3. Call `updateRSETHPrice()` with `totalETHInProtocol = 1100e18`. Assert treasury receives rsETH ≈ 10 ETH / 1.09 ≈ 9.17 rsETH. Assert `rsETHPrice ≈ 1.09e18`.
4. Call `updateRSETHPrice()` with `totalETHInProtocol = 1020e18`. Assert no fee minted. Assert `rsETHPrice ≈ 1.02e18`.
5. Call `updateRSETHPrice()` with `totalETHInProtocol = 1100e18`. Assert treasury receives rsETH ≈ 8 ETH / 1.092 ≈ 7.33 rsETH (the over-fee).
6. Assert that the correct fee should be ≈ 1 ETH / 1.092 ≈ 0.92 rsETH (10% of the 10 ETH net gain above the prior fee-bearing TVL of 1090).
7. The difference (~6.4 rsETH) represents depositor yield stolen by the treasury, confirming the finding.

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L233-234)
```text
        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);
```

**File:** contracts/LRTOracle.sol (L244-246)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
```

**File:** contracts/LRTOracle.sol (L270-281)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```
