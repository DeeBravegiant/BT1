Audit Report

## Title
Protocol Fee Charged on Gross TVL Recovery Instead of Net Gain Above Fee-Adjusted Baseline — (`contracts/LRTOracle.sol`)

## Summary

`_updateRsETHPrice()` computes `previousTVL` as `rsethSupply × rsETHPrice`, where `rsETHPrice` is the post-fee, post-loss stored price. After a loss, `rsETHPrice` falls, lowering the fee baseline. On recovery, the fee is charged on the full gross recovery above the depressed baseline rather than only on net new gains above the prior fee-bearing TVL level. The treasury mints rsETH representing ETH that economically belongs to depositors, diluting all rsETH holders.

## Finding Description

In `_updateRsETHPrice()`, the fee baseline is computed at line 234:

```solidity
uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);
```

`rsETHPrice` is the value stored at the end of the previous call (line 313: `rsETHPrice = newRsETHPrice`). After a loss, `newRsETHPrice = totalETHInProtocol / rsethSupply` is lower, so `rsETHPrice` is stored at the reduced level. On the next call after recovery, `previousTVL` is computed from this lower price, creating a depressed baseline. The fee at lines 244–246 is then charged on `totalETHInProtocol - previousTVL`, which includes the full recovery of the prior loss, not just net new yield.

The `highestRsethPrice` variable (line 30) is not used in the fee calculation — it only governs the price-change threshold check (lines 252–267) and the downside-protection pause (lines 270–282). The downside protection only triggers if `pricePercentageLimit > 0` and the drop exceeds that limit; small losses below the threshold proceed without a pause, and `rsETHPrice` is updated to the lower value, permanently lowering the fee baseline.

Concrete trace (10% fee rate):
- Step 1: TVL = 1000, `rsETHPrice` = 1.0 → `previousTVL` = 1000
- Step 2: TVL = 1100 → fee on 100 → `rsETHPrice` stored ≈ 1.09
- Step 3: TVL = 1020 → `previousTVL` = 1090 → no fee → `rsETHPrice` stored ≈ 1.02
- Step 4: TVL = 1100 → `previousTVL` = 1020 → fee on 80 → **8 ETH fee**

Correct fee at step 4 (net gain since last fee = 10 ETH): 1 ETH. Overcharge: **7 ETH** minted to treasury at depositors' expense.

Existing guards are insufficient: the `protocolPaused` check (line 240) only blocks fee minting when the protocol is paused; it does not correct the baseline. The daily fee mint cap (`maxFeeMintAmountPerDay`) limits magnitude per day but does not prevent the structural overcharge.

## Impact Explanation

This is **theft of unclaimed yield** (High severity). The treasury receives rsETH minted against ETH that represents recovery of depositor principal, not new yield. Every existing rsETH holder is diluted: their share of the protocol's ETH is reduced by the excess fee. The magnitude scales with loss size and `protocolFeeInBPS` (up to 15% per `setProtocolFeeBps`). The effect is permanent and cumulative across repeated loss-recovery cycles.

## Likelihood Explanation

EigenLayer restaking strategies are subject to slashing and market-price fluctuations, making partial loss-recovery cycles a realistic and recurring scenario. `updateRSETHPrice()` is a public, permissionless function (line 87) — no privileged actor is required. Any address can call it at the moment TVL recovers above the depressed baseline to trigger the excess fee mint. If `pricePercentageLimit` is zero (its default after `initialize`), there is no downside-protection pause at all, making every loss-recovery cycle exploitable.

## Recommendation

Introduce a `feeAdjustedHighWaterMark` state variable that is set to `totalETHInProtocol` after each fee-bearing update and is **never decreased** on a loss. Replace the `previousTVL` computation with:

```solidity
uint256 previousTVL = feeAdjustedHighWaterMark;
```

Only charge fees on TVL increases above this mark, and update it to `totalETHInProtocol` (post-fee) only when a fee is taken. This ensures that a recovery to a previously-taxed TVL level is not taxed again.

## Proof of Concept

Foundry test outline:

```solidity
// 1. Deploy LRTOracle with 10% protocol fee (1000 BPS)
// 2. Mint 1000 rsETH; set mock totalETHInProtocol = 1000e18
// 3. Call updateRSETHPrice() → rsETHPrice ≈ 1.0e18, no fee (TVL == previousTVL)
// 4. Set mock totalETHInProtocol = 1100e18
// 5. Call updateRSETHPrice() → fee = 10 ETH, rsETHPrice ≈ 1.09e18
//    Assert: treasury rsETH balance > 0; rsETHPrice ≈ 1.09e18
// 6. Set mock totalETHInProtocol = 1020e18 (loss)
// 7. Call updateRSETHPrice() → no fee (1020 < 1090), rsETHPrice ≈ 1.02e18
// 8. Set mock totalETHInProtocol = 1100e18 (recovery)
// 9. Call updateRSETHPrice() → fee charged on (1100 - 1020) = 80 ETH → 8 ETH fee
//    Assert: treasury received ~8 ETH worth of rsETH
//    Assert: correct fee should be ~1 ETH (10% of net gain 10 ETH)
//    Assert: overcharge = ~7 ETH → FAIL under correct high-water-mark accounting
```

Entry: `LRTOracle.updateRSETHPrice()` (public, no access control) called by any address after a loss-recovery cycle. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L233-247)
```text
        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);

        IPausable lrtDepositPool = IPausable(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        IPausable withdrawalManager = IPausable(lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER));

        // determine if the protocol is active (not paused)
        bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;

        // only take fee if TVL increased and protocol is not paused
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }
```

**File:** contracts/LRTOracle.sol (L299-313)
```text
        if (protocolFeeInETH > 0) {
            // Calculate rsETH amount to mint as protocol fee
            uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);

            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
            if (rsethAmountToMintAsProtocolFee > 0) {
                address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
                IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
                emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
            }
        } else {
            _checkAndUpdateDailyFeeMintLimit(0);
        }

        rsETHPrice = newRsETHPrice;
```
