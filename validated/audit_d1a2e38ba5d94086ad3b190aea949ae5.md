Audit Report

## Title
`_checkAndUpdateDailyFeeMintLimit` Reverts When `maxFeeMintAmountPerDay` Is Zero and Protocol Fees Are Due — (File: `contracts/LRTOracle.sol`)

## Summary

`maxFeeMintAmountPerDay` defaults to `0` and is never set in `initialize` or `reinitialize`. When TVL increases and `protocolFeeInBPS > 0`, `_checkAndUpdateDailyFeeMintLimit` is called with a non-zero `feeAmount`, triggering an unconditional revert because `feeAmount > 0 == maxFeeMintAmountPerDay`. This permanently blocks `updateRSETHPrice()` — a public, permissionless function — freezing unclaimed protocol yield and leaving the rsETH price stale until a privileged `setMaxFeeMintAmountPerDay` call is made.

## Finding Description

`_checkAndUpdateDailyFeeMintLimit` at L205–207 performs:

```solidity
if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
    revert DailyFeeMintLimitExceeded(...);
}
```

When `maxFeeMintAmountPerDay == 0` (Solidity default) and `feeAmount > 0`, the condition reduces to `feeAmount > 0`, which is always true, causing an unconditional revert.

`feeAmount > 0` occurs whenever `protocolFeeInETH > 0` (L299–303), which occurs whenever TVL has grown and the protocol is not paused (L244–246):

```solidity
if (!protocolPaused && totalETHInProtocol > previousTVL) {
    uint256 rewardAmount = totalETHInProtocol - previousTVL;
    protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
}
```

Neither `initialize` (L64–68) nor `reinitialize` (L72–79) sets `maxFeeMintAmountPerDay`. It must be set separately via `setMaxFeeMintAmountPerDay` (L132–135). Any deployment or upgrade that omits this call leaves the contract in the broken state.

The inconsistency is confirmed by `remainingDailyFeeMintLimit` (L171), which has an explicit zero-guard:

```solidity
if (maxFeeMintAmountPerDay == 0) return 0;
```

The write path (`_checkAndUpdateDailyFeeMintLimit`) has no equivalent guard.

Call chain:
```
updateRSETHPrice() [public, permissionless]
  → _updateRsETHPrice()
      → _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee)  ← reverts
```

## Impact Explanation

**Medium — Permanent freezing of unclaimed yield.**

While `maxFeeMintAmountPerDay == 0`, every call to the public `updateRSETHPrice()` reverts whenever TVL has grown. Protocol fees (`protocolFeeInBPS` of all yield) are never minted to the treasury. The rsETH price goes stale, allowing new depositors to receive more rsETH than entitled at the expense of existing holders. The freeze persists indefinitely until a privileged `setMaxFeeMintAmountPerDay` call is made. This matches the allowed impact: **Medium — Permanent freezing of unclaimed yield**.

## Likelihood Explanation

`maxFeeMintAmountPerDay` is `0` by default and absent from both `initialize` and `reinitialize`. Any deployment or upgrade that does not immediately call `setMaxFeeMintAmountPerDay` is vulnerable. The trigger is entirely unprivileged: any external caller invoking `updateRSETHPrice()` after TVL has increased will hit the revert. No malicious intent is required — a legitimate price-update call (e.g., by a keeper bot) is sufficient.

## Recommendation

Add a zero-guard in `_checkAndUpdateDailyFeeMintLimit` consistent with the existing guard in `remainingDailyFeeMintLimit`:

```solidity
function _checkAndUpdateDailyFeeMintLimit(uint256 feeAmount) internal {
+   if (maxFeeMintAmountPerDay == 0) return; // limit not configured; skip enforcement
    ...
}
```

Alternatively, require `maxFeeMintAmountPerDay` to be set to a non-zero value during `reinitialize` so the invariant is enforced at upgrade time.

## Proof of Concept

1. Deploy `LRTOracle` and call `reinitialize` without subsequently calling `setMaxFeeMintAmountPerDay`. `maxFeeMintAmountPerDay` remains `0`.
2. Users deposit assets into `LRTDepositPool`, increasing TVL above `rsETHPrice × rsETHSupply`.
3. Any user calls `updateRSETHPrice()`.
4. Inside `_updateRsETHPrice`, `totalETHInProtocol > previousTVL` and `protocolFeeInBPS > 0`, so `protocolFeeInETH > 0`.
5. `rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice) > 0`.
6. `_checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee)` evaluates `0 + rsethAmountToMintAsProtocolFee > 0` → **reverts with `DailyFeeMintLimitExceeded`**.
7. `updateRSETHPrice()` reverts. rsETH price is not updated. Protocol fee is not minted. This repeats on every subsequent call as long as `maxFeeMintAmountPerDay == 0`.

**Foundry test sketch:**
```solidity
function test_updateRSETHPrice_revertsWhenMaxFeeMintAmountPerDayIsZero() public {
    // maxFeeMintAmountPerDay is 0 (never set)
    // simulate TVL increase (mock getTotalAssetDeposits to return higher value)
    vm.expectRevert(LRTOracle.DailyFeeMintLimitExceeded.selector);
    lrtOracle.updateRSETHPrice();
}
```