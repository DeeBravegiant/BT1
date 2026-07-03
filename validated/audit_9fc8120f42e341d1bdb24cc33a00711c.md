Looking at the code carefully to trace the exact execution path and determine if the vulnerability is real.

### Title
Fee-Before-Price Ordering in `_updateRsETHPrice` Allows Block Stuffing to Permanently Freeze `rsETHPrice` via `DailyFeeMintLimitExceeded` - (`contracts/LRTOracle.sol`)

---

### Summary

`_updateRsETHPrice()` computes the protocol fee on **all accumulated rewards since the last price update**, then calls `_checkAndUpdateDailyFeeMintLimit()` at line 303 **before** writing `rsETHPrice` at line 313. If an attacker uses block stuffing to prevent `updateRSETHPrice()` from being called for ≥2 periods, the multi-period fee will exceed `maxFeeMintAmountPerDay`, causing the call to revert with `DailyFeeMintLimitExceeded` and leaving `rsETHPrice` permanently stale until the manager intervenes.

---

### Finding Description

The execution path in `_updateRsETHPrice()` is:

1. **Line 244–246**: `rewardAmount = totalETHInProtocol - previousTVL` — this is the **total** reward accumulated since the last successful price update, not one day's worth.
2. **Line 301**: `rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice)` — the fee rsETH amount scales linearly with the number of missed periods.
3. **Line 303**: `_checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee)` — the daily limit check fires **before** the price is committed.
4. **Line 313**: `rsETHPrice = newRsETHPrice` — **never reached** if the check at line 303 reverts.

Inside `_checkAndUpdateDailyFeeMintLimit()`:

```solidity
// line 199-202: resets to 0 for a new period — but feeAmount is N-days worth
if (block.timestamp >= feePeriodStartTime + 1 days) {
    currentPeriodMintedFeeAmount = 0;
    feePeriodStartTime = getCurrentPeriodStartTime();
}
// line 205-207: reverts if N-day fee > 1-day limit
if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
    revert DailyFeeMintLimitExceeded(...);
}
```

The period reset correctly zeroes `currentPeriodMintedFeeAmount`, but `feeAmount` passed in is the **entire multi-period fee**. If the oracle was not updated for N days, `feeAmount ≈ N × dailyFee`. When `N ≥ 2` and `maxFeeMintAmountPerDay` is calibrated to one day's fee, the check reverts unconditionally. [1](#0-0) [2](#0-1) 

---

### Impact Explanation

- `rsETHPrice` storage is never updated (line 313 is unreachable while the revert condition holds).
- `LRTDepositPool.getRsETHAmountToMint()` divides by `lrtOracle.rsETHPrice()` — a stale price means all deposit minting uses an incorrect (undervalued) exchange rate, or the protocol is effectively bricked for pricing.
- The freeze persists until the manager calls `setMaxFeeMintAmountPerDay()` with a higher value, which is an out-of-band admin action. [3](#0-2) 

Impact: **Low — Block stuffing** (rsETHPrice frozen; temporary until manager raises the daily limit).

---

### Likelihood Explanation

Block stuffing for 2+ consecutive days on Ethereum mainnet is extremely capital-intensive (filling every block at the gas limit for 48+ hours). However:

- The precondition only requires **2 missed periods** — a single day of stuffing suffices if `maxFeeMintAmountPerDay` is set tightly.
- The same freeze can be triggered by an **unintentional keeper outage** (no attacker required), making the code path reachable in normal operational failure scenarios.
- `maxFeeMintAmountPerDay` is set by the manager and can be calibrated to exactly one day's expected fee, making the off-by-one condition easy to hit.

---

### Recommendation

Move `rsETHPrice = newRsETHPrice` **before** the fee check, or restructure the fee check to cap the minted fee at `maxFeeMintAmountPerDay` (carry-forward the remainder) rather than reverting. A non-reverting cap ensures the price is always updated even when the fee exceeds the daily limit:

```solidity
// Option A: cap fee, never revert
uint256 cappedFee = _capAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
// mint cappedFee instead of full fee

rsETHPrice = newRsETHPrice; // always reached
```

Alternatively, split the fee accumulation across multiple virtual periods proportionally to the elapsed time.

---

### Proof of Concept

```solidity
// Foundry test (local fork or mock)
function test_blockStuffingFreezesRsETHPrice() public {
    // Setup: maxFeeMintAmountPerDay = 1 day's expected fee
    oracle.setMaxFeeMintAmountPerDay(DAILY_FEE);

    // Simulate 2 days passing without updateRSETHPrice being called
    // (block stuffing prevents keeper from landing the tx)
    vm.warp(block.timestamp + 2 days);

    // Underlying assets accrue 2 days of staking rewards
    // (mock oracle returns higher TVL)

    uint256 priceBefore = oracle.rsETHPrice();

    // First call after stuffing: fee = 2x daily fee > maxFeeMintAmountPerDay
    vm.expectRevert(ILRTOracle.DailyFeeMintLimitExceeded.selector);
    oracle.updateRSETHPrice();

    // rsETHPrice is unchanged — frozen
    assertEq(oracle.rsETHPrice(), priceBefore);
}
``` [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L197-210)
```text
    function _checkAndUpdateDailyFeeMintLimit(uint256 feeAmount) internal {
        // Reset the period if it's unset or a day has passed
        if (block.timestamp >= feePeriodStartTime + 1 days) {
            currentPeriodMintedFeeAmount = 0;
            feePeriodStartTime = getCurrentPeriodStartTime();
        }

        // Check if minting would exceed the daily limit
        if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
            revert DailyFeeMintLimitExceeded(currentPeriodMintedFeeAmount + feeAmount, maxFeeMintAmountPerDay);
        }

        currentPeriodMintedFeeAmount += feeAmount;
    }
```

**File:** contracts/LRTOracle.sol (L244-247)
```text
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

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
