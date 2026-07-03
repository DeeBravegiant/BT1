Audit Report

## Title
Stale `getEffectivePodShares()` During Incomplete EigenLayer Checkpoint Inflates `highestRsethPrice`, Triggering Automatic Protocol Pause on Checkpoint Completion — (`contracts/LRTOracle.sol`)

## Summary

`LRTOracle.updateRSETHPrice()` computes rsETH price using TVL that includes `getEffectivePodShares()` per NodeDelegator, which reads `DelegationManager.getWithdrawableShares()`. This value is only updated when an EigenPod checkpoint finalizes. During an incomplete checkpoint (after beacon-chain slashing, while `proofsRemaining > 0`), the withdrawable-share figure still reflects the pre-slashing balance. If `updateRSETHPrice()` is called during this window, `highestRsethPrice` is set to the pre-slashing (inflated) value. When the checkpoint later finalizes and the true lower balance is reflected, the next `updateRSETHPrice()` call computes a price below `highestRsethPrice` by more than `pricePercentageLimit`, automatically pausing `LRTDepositPool` and `LRTWithdrawalManager`.

## Finding Description

**TVL source during an incomplete checkpoint**

`LRTOracle._updateRsETHPrice()` calls `_getTotalEthInProtocol()`, which iterates over supported assets and calls `ILRTDepositPool.getTotalAssetDeposits(asset)` for each. For native ETH, this aggregation ultimately reaches `INodeDelegator(nodeDelegatorQueue[i]).getEffectivePodShares()` (confirmed at `LRTDepositPool.sol:487`).

`getEffectivePodShares()` returns `stakedButUnverifiedNativeETH + withdrawableShare`, where `withdrawableShare` is fetched via `NodeDelegatorHelper.getWithdrawableShare()` → `DelegationManager.getWithdrawableShares()`. For beacon-chain ETH, EigenLayer computes this as `podOwnerDepositShares × scalingFactors`. `podOwnerDepositShares` is **only updated when a checkpoint finalizes** via `recordBeaconChainETHBalanceUpdate`. During an incomplete checkpoint (`proofsRemaining > 0`), this value still reflects the last finalized (pre-slashing) balance.

**`highestRsethPrice` is set from pre-slashing TVL**

`_updateRsETHPrice()` computes `newRsETHPrice` from the stale (pre-slashing) TVL and updates `highestRsethPrice` if the new price exceeds it:

```solidity
// LRTOracle.sol:294-296
if (newRsETHPrice > highestRsethPrice) {
    highestRsethPrice = newRsETHPrice;
}
```

If `updateRSETHPrice()` is called while the checkpoint is incomplete, `highestRsethPrice` is anchored to the pre-slashing price.

**Checkpoint finalizes, price corrects, pause triggers**

Once all `verifyCheckpointProofs` calls are submitted and the checkpoint finalizes, `podOwnerDepositShares` drops to reflect the actual slashed balance. The next call to `updateRSETHPrice()` computes a lower `newRsETHPrice`. The downside protection logic:

```solidity
// LRTOracle.sol:270-281
if (newRsETHPrice < highestRsethPrice) {
    uint256 diff = highestRsethPrice - newRsETHPrice;
    bool isPriceDecreaseOffLimit =
        pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);
    if (isPriceDecreaseOffLimit) {
        if (!lrtDepositPool.paused()) lrtDepositPool.pause();
        if (!withdrawalManager.paused()) withdrawalManager.pause();
        _pause();
        return;
    }
}
```

automatically pauses `LRTDepositPool` and `LRTWithdrawalManager`.

**No automated recovery**

`unpause()` on both contracts is `onlyLRTAdmin`, requiring manual admin intervention with no automated recovery path.

**Why existing checks fail**

The upside guard (lines 252–266) only reverts if the price *increase* exceeds `pricePercentageLimit`. During the incomplete checkpoint window, the price increase from yield is within normal bounds, so `highestRsethPrice` is updated without triggering a revert. The vulnerability lies in the asymmetry: the upside guard prevents large single-step increases but does not prevent `highestRsethPrice` from being set to a value that will later cause a large downside trigger when the checkpoint finalizes.

## Impact Explanation

All user deposits (`LRTDepositPool.depositETH`, `depositAsset`) and all withdrawals (`LRTWithdrawalManager.initiateWithdrawal`, `claimWithdrawal`) are blocked until admin manually unpauses. This constitutes **temporary freezing of funds** for all protocol users, matching the Medium allowed impact scope.

## Likelihood Explanation

- Beacon-chain slashing is a real, documented event; correlation penalties can be significant across a large validator set.
- `startCheckpoint` is `onlyLRTOperator`, but the operator would naturally initiate a checkpoint after slashing to reconcile balances — this is expected operational behavior, not an attacker-controlled precondition.
- `verifyCheckpointProofs` is permissionless but requires multiple transactions for large validator sets, creating a meaningful time window.
- `updateRSETHPrice()` is a public, permissionless function callable by anyone (including bots and keepers), making it trivial to call during the incomplete-checkpoint window.
- The combination of slashing + large validator set + public price update is realistic and non-contrived.

## Recommendation

1. **Checkpoint-aware price guard**: In `_updateRsETHPrice()`, check whether any NodeDelegator's EigenPod has an active checkpoint (`eigenPod.currentCheckpointTimestamp() != 0`). If so, skip updating `highestRsethPrice` (or skip the entire price update) until the checkpoint is finalized.
2. **Decouple `highestRsethPrice` update from stale reads**: Only update `highestRsethPrice` when the price increase is verified to originate from real yield, not from a stale pre-checkpoint read.
3. **Operator tooling**: Ensure operators complete checkpoints promptly after slashing events to minimize the stale-data window.

## Proof of Concept

```
Foundry fork test outline (mainnet fork, no public-mainnet state changes):

1. Fork mainnet with a NodeDelegator that has N active validators (e.g., 100).
2. Simulate beacon-chain slashing: reduce podOwnerDepositShares in EigenPodManager
   storage to reflect a 10% slash (via vm.store or EigenLayer test harness).
3. Call NodeDelegator.startCheckpoint(false) as operator —
   checkpoint starts, proofsRemaining = N.
4. Call LRTOracle.updateRSETHPrice() as any unprivileged address.
   Assert: highestRsethPrice == pre_slashing_price (inflated value set).
5. Submit all verifyCheckpointProofs — checkpoint finalizes,
   podOwnerDepositShares drops by 10%.
6. Call LRTOracle.updateRSETHPrice() again as any unprivileged address.
   Assert: newRsETHPrice < highestRsethPrice by > pricePercentageLimit.
   Assert: LRTDepositPool.paused() == true.
   Assert: LRTWithdrawalManager.paused() == true.
   Assert: LRTOracle.paused() == true.
```