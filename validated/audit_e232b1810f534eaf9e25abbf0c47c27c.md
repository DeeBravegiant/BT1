Audit Report

## Title
Block Stuffing Suppresses rsETH Price by Preventing `startCheckpoint`, Excluding Uncheckpointed Pod ETH from TVL — (`contracts/NodeDelegator.sol`, `contracts/LRTOracle.sol`)

## Summary
ETH that arrives at the EigenPod via beacon-chain partial-withdrawal sweeps or execution-layer rewards is not reflected in `getEffectivePodShares()` until `startCheckpoint` is called and the resulting checkpoint is fully proven. Because `startCheckpoint` is gated behind `onlyLRTOperator`, an attacker who block-stuffs to prevent the operator's transaction from landing can hold the rsETH price suppressed for the duration of the stuffing window, then mint rsETH at the artificially low price and profit when the price corrects after stuffing ends.

## Finding Description

**Root cause — uncheckpointed pod ETH is invisible to TVL.**

`NodeDelegator.getEffectivePodShares()` returns only `stakedButUnverifiedNativeETH + withdrawableShare`, where `withdrawableShare` is sourced from `DelegationManager.getWithdrawableShares()` via `NodeDelegatorHelper.getWithdrawableShare()`. EigenLayer's `DelegationManager` reflects only finalized checkpoint shares; raw ETH sitting in the pod (emitted as `NonBeaconChainETHReceived`) is not included until `startCheckpoint` → `verifyCheckpointProofs` → checkpoint finalization completes.

```
NodeDelegator.getEffectivePodShares()          // L556-562
  └─ NodeDelegatorHelper.getWithdrawableShare() // L52-65
       └─ DelegationManager.getWithdrawableShares()  // only finalized shares
```

**Propagation to price.**

`LRTDepositPool.getETHDistributionData()` accumulates `getEffectivePodShares()` as `ethStakedInEigenLayer` (L484-493). `LRTOracle._updateRsETHPrice()` derives `totalETHInProtocol` from this sum (L231-250) and divides by `rsethSupply` to produce `rsETHPrice`. Uncheckpointed pod ETH is absent from the numerator, so the price is understated by exactly that amount.

**Operator-only gate enables block stuffing.**

The only in-scope entry point to trigger a checkpoint is:

```solidity
function startCheckpoint(bool revertIfNoBalance) external onlyLRTOperator {
    eigenPod.startCheckpoint(revertIfNoBalance);
}
```
(L259-261, `contracts/NodeDelegator.sol`)

An attacker who fills every block with high-gas transactions prevents the operator's `startCheckpoint` call from landing. During this window the pod's raw ETH balance is excluded from `getEffectivePodShares()`, so `rsETHPrice` is lower than the true per-token backing.

**Exploit path.**

1. Attacker observes ETH accumulating in the EigenPod (beacon rewards, partial withdrawals).
2. Attacker begins block stuffing to prevent the operator from calling `startCheckpoint`.
3. `rsETHPrice` is set below true backing on each `_updateRsETHPrice()` call.
4. Attacker calls `LRTDepositPool.depositETH()` (public), minting rsETH at the suppressed price — receiving more rsETH per ETH than the true backing warrants.
5. Attacker stops stuffing; operator calls `startCheckpoint` and finalizes the checkpoint.
6. `rsETHPrice` corrects upward. Attacker's rsETH is now worth more ETH than they paid, at the expense of existing holders who were diluted.

**Existing guards are insufficient.**

The `PriceAboveDailyThreshold` guard (L252-266) limits how fast the price can rise after correction, potentially requiring a manager call to push the price through — further delaying correct price discovery and extending the dilution window rather than preventing it.

## Impact Explanation

**Low — Block stuffing / Contract fails to deliver promised returns.**

During the stuffing window, existing rsETH holders are diluted: new depositors mint rsETH at a price below true backing, receiving excess rsETH. When the checkpoint is eventually finalized, the price corrects, but the dilution to existing holders is permanent. The `PriceAboveDailyThreshold` guard may additionally delay full price recovery, compounding the harm.

## Likelihood Explanation

Block stuffing on Ethereum mainnet is expensive but economically rational when the attacker can accumulate a large rsETH position at a discount and profit from the subsequent price correction. The structural gap (uncheckpointed pod ETH invisible to TVL) is permanent and requires no special preconditions beyond ETH accruing to the pod, which happens continuously via beacon-chain rewards. The operator has no on-chain mechanism to bypass block stuffing; they can only wait or use a private mempool relay, which is not enforced by the protocol.

## Recommendation

1. **Include raw pod ETH in `getEffectivePodShares()`**: add `address(eigenPod).balance` (minus `withdrawableRestakedExecutionLayerGwei` already counted as shares) to the return value so uncheckpointed ETH is always reflected in TVL.
2. **Alternatively**, read `IEigenPod.currentCheckpoint().podBalanceGwei` and add it to the share count when a checkpoint is in progress.
3. **Operational hardening**: require the operator to use a private/protected mempool relay (e.g., Flashbots `eth_sendPrivateTransaction`) for `startCheckpoint` calls to make block stuffing ineffective.

## Proof of Concept

```solidity
// Foundry fork test (mainnet fork)
// 1. Fork mainnet with an active NodeDelegator + EigenPod.
// 2. Simulate beacon-chain partial withdrawal arriving at the pod:
//    vm.deal(address(eigenPod), address(eigenPod).balance + 1 ether);
// 3. Confirm getEffectivePodShares() does NOT include the 1 ETH:
//    uint256 sharesBefore = nodeDelegator.getEffectivePodShares();
//    // sharesBefore is unchanged — 1 ETH is invisible
// 4. Call LRTOracle.updateRSETHPrice() and record rsETHPrice_suppressed.
// 5. As attacker, call LRTDepositPool.depositETH{value: X}() — mints rsETH at suppressed price.
// 6. As operator, call startCheckpoint(false) + verifyCheckpointProofs to finalize.
// 7. Call LRTOracle.updateRSETHPrice() and record rsETHPrice_corrected.
// 8. Assert rsETHPrice_corrected > rsETHPrice_suppressed.
// 9. Assert attacker's rsETH balance * rsETHPrice_corrected > X ether paid — proving dilution profit.
//
// Block-stuffing simulation: skip step 6 indefinitely and show rsETHPrice remains
// suppressed as long as startCheckpoint is not called.
```