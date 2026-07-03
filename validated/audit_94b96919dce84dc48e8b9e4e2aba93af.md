Audit Report

## Title
`maxFeeMintAmountPerDay` Uninitialized Causes `updateRSETHPrice()` to Revert When Protocol Fee Is Active — (`contracts/LRTOracle.sol`)

## Summary

`LRTOracle` declares `maxFeeMintAmountPerDay` at line 35 but never assigns it in `initialize()` (L64–68) or `reinitialize()` (L72–79), leaving it at `0`. When `protocolFeeInBPS > 0` and TVL grows, `_updateRsETHPrice()` computes a positive `rsethAmountToMintAsProtocolFee` and passes it to `_checkAndUpdateDailyFeeMintLimit()`, which unconditionally reverts because `feeAmount > 0 == maxFeeMintAmountPerDay`. Every call to the public `updateRSETHPrice()` fails, and the treasury never receives its fee share.

## Finding Description

**State variable declaration** (`contracts/LRTOracle.sol`, L32–35): `maxFeeMintAmountPerDay` is declared alongside the other daily-fee variables but receives no value in either initializer.

**`initialize()`** (L64–68) sets only `lrtConfig`; no fee variables are touched.

**`reinitialize()`** (L72–79) sets `feePeriodStartTime` but omits `maxFeeMintAmountPerDay`, leaving it `0`.

**`_checkAndUpdateDailyFeeMintLimit()`** (L197–210): after optionally resetting the period, it evaluates:
```solidity
if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
    revert DailyFeeMintLimitExceeded(...);
}
```
With `maxFeeMintAmountPerDay == 0` and any `feeAmount > 0`, the condition is `feeAmount > 0` — always `true` → always reverts.

**`_updateRsETHPrice()`** (L298–311): when `protocolFeeInETH > 0` (i.e., `protocolFeeInBPS > 0` and TVL increased), it calls `_checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee)` unconditionally at L303, triggering the revert.

**Asymmetry confirmed** by `remainingDailyFeeMintLimit()` (L170–181), which explicitly guards `if (maxFeeMintAmountPerDay == 0) return 0;` — the view function handles the zero case gracefully, but the internal write function does not.

**Exploit path:**
1. `initialize(lrtConfigAddr)` — `maxFeeMintAmountPerDay` remains `0`.
2. `reinitialize(_feePeriodStartTime)` — `feePeriodStartTime` set; `maxFeeMintAmountPerDay` still `0`.
3. `LRTConfig.setProtocolFeeBps(500)` — fee enabled.
4. EigenLayer rewards accrue; `totalETHInProtocol > previousTVL`.
5. Any address calls `updateRSETHPrice()` (public, L87) → `_updateRsETHPrice()` → `protocolFeeInETH > 0` → `_checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee)` → **`DailyFeeMintLimitExceeded` revert**.
6. Price is never updated; treasury never receives fees. All yield accrued during this window is permanently unrecoverable.

## Impact Explanation

**Medium — Permanent freezing of unclaimed yield.** The treasury's rsETH fee share is never minted for any period during which `maxFeeMintAmountPerDay` remains `0`. Because fee minting is not retroactive, the yield that accrued between `reinitialize()` and a subsequent `setMaxFeeMintAmountPerDay()` call is permanently lost to the protocol treasury. This matches the allowed impact "Permanent freezing of unclaimed yield."

## Likelihood Explanation

The normal production configuration has `protocolFeeInBPS > 0` and TVL growing continuously from staking rewards. The `reinitialize()` function is the natural entry point for enabling the fee feature; it sets `feePeriodStartTime` but silently omits `maxFeeMintAmountPerDay`. An operator following the upgrade path will reach the broken state without any on-chain error at initialization time. No attacker capability is required — the revert is triggered by any public caller (or a keeper bot) invoking `updateRSETHPrice()`.

## Recommendation

Initialize `maxFeeMintAmountPerDay` inside `reinitialize()`:

```solidity
function reinitialize(uint256 _feePeriodStartTime, uint256 _maxFeeMintAmountPerDay)
    external reinitializer(2) onlyLRTManager
{
    ...
    feePeriodStartTime = _feePeriodStartTime;
    maxFeeMintAmountPerDay = _maxFeeMintAmountPerDay;
    emit MaxFeeMintAmountPerDayUpdated(_maxFeeMintAmountPerDay);
}
```

Alternatively, align `_checkAndUpdateDailyFeeMintLimit()` with `remainingDailyFeeMintLimit()` by treating `maxFeeMintAmountPerDay == 0` as "no cap":

```solidity
if (maxFeeMintAmountPerDay == 0) return; // cap not yet configured
```

## Proof of Concept

**Foundry unit test outline:**

```solidity
function test_updateRSETHPrice_revertsWhenFeeActiveAndMaxNotSet() public {
    // 1. deploy LRTOracle proxy, call initialize(lrtConfigAddr)
    // 2. call reinitialize(block.timestamp - 1 hours) as manager
    //    → feePeriodStartTime set, maxFeeMintAmountPerDay == 0
    // 3. lrtConfig.setProtocolFeeBps(500) as admin
    // 4. mock totalETHInProtocol > previousTVL (simulate reward accrual)
    // 5. vm.expectRevert(LRTOracle.DailyFeeMintLimitExceeded.selector);
    //    oracle.updateRSETHPrice();
}
```

The revert is deterministic: any positive `rsethAmountToMintAsProtocolFee` against `maxFeeMintAmountPerDay == 0` satisfies `feeAmount > 0` at L205, triggering `DailyFeeMintLimitExceeded`.