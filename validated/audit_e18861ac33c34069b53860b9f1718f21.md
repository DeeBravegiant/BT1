Audit Report

## Title
`initiateWithdrawal` Reverts When `assetsCommitted >= totalAssets`, Temporarily Blocking Withdrawal Queue Entry - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`LRTWithdrawalManager.initiateWithdrawal` enforces a capacity check that reverts with `ExceedAmountToWithdraw` whenever `assetsCommitted[asset] >= lrtDepositPool.getTotalAssetDeposits(asset)`. Because `assetsCommitted` is only decremented inside `_unlockWithdrawalRequests`, which is called exclusively by the privileged `unlockQueue` function and only after `withdrawalDelayBlocks` have elapsed per request, users arriving after capacity is saturated are completely blocked from entering the withdrawal queue for up to 8–16 days with no on-chain recourse.

## Finding Description
In `initiateWithdrawal` (L166–173), rsETH is transferred from the user before the capacity check:

```solidity
IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
assetsCommitted[asset] += expectedAssetAmount;
```

`getAvailableAssetAmount` (L599–603) computes:

```solidity
uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
```

Once `assetsCommitted[asset]` reaches `totalAssets`, `getAvailableAssetAmount` returns `0`, and any non-zero `expectedAssetAmount` causes a revert. The only path to reduce `assetsCommitted` is `_unlockWithdrawalRequests` (L802):

```solidity
assetsCommitted[asset] -= request.expectedAssetAmount;
```

This is called exclusively from `unlockQueue` (L268–320), which is gated by `onlyAssetTransferOrOperatorRole` (L280) and internally enforces the delay check (L795):

```solidity
if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;
```

This means even a willing operator cannot reduce `assetsCommitted` until `withdrawalDelayBlocks` have elapsed since the oldest pending request. The default is `8 days / 12 seconds` (L94) and the cap is `16 days / 12 seconds` (L340). The transaction revert undoes the `safeTransferFrom`, so the user retains their rsETH — but they are completely blocked from entering the withdrawal queue for the entire delay window.

**Exploit path:**
1. Protocol holds N total assets; users call `initiateWithdrawal` until `assetsCommitted[asset] = N`.
2. Any subsequent caller receives `ExceedAmountToWithdraw` regardless of rsETH held.
3. The operator cannot call `unlockQueue` to reduce `assetsCommitted` until the delay passes for the oldest request.
4. With the default 8-day delay, latecomers are locked out of the withdrawal queue for up to 8–16 days.

`instantWithdrawal` (L212–253) is an alternative exit path but requires `isInstantWithdrawalEnabled[asset] == true`, which is a manager-controlled toggle and not guaranteed to be active.

## Impact Explanation
Users holding rsETH cannot queue a withdrawal request when `assetsCommitted[asset] >= totalAssets`. Since `initiateWithdrawal` is the standard entry point for the withdrawal lifecycle, affected users are temporarily unable to begin the exit process through the protocol's primary mechanism. The freeze duration is bounded by the operator's `unlockQueue` cadence plus the withdrawal delay — up to 16 days per the contract's own cap. This constitutes **temporary freezing of funds** (Medium).

## Likelihood Explanation
The scenario is reachable by any set of unprivileged depositors acting independently through normal `initiateWithdrawal` calls. During periods of high exit demand (e.g., a depeg event, EigenLayer slashing news, or a competing yield opportunity), many users will race to call `initiateWithdrawal`. Once capacity is saturated, every subsequent caller is blocked with no on-chain recourse. No admin compromise or special privilege is required to trigger the condition.

## Recommendation
Decouple the capacity check from the ability to queue a withdrawal request. Two complementary approaches:

1. **Allow over-queuing with deferred settlement:** Remove the `ExceedAmountToWithdraw` revert from `initiateWithdrawal` and instead enforce the capacity constraint inside `_unlockWithdrawalRequests`, so requests are accepted into the queue but only settled when assets become available.
2. **Reduce `assetsCommitted` eagerly:** Allow `unlockQueue` to be called by anyone (not just the operator) once `withdrawalDelayBlocks` have elapsed, so the committed capacity is freed without depending on operator liveness.

## Proof of Concept

1. Protocol holds 1,000 ETH total (`getTotalAssetDeposits(ETH) = 1000e18`).
2. Users A–Z call `initiateWithdrawal(ETH, ...)` in sequence; after their requests are accepted, `assetsCommitted[ETH] = 1000e18`.
3. User Bob calls `initiateWithdrawal(ETH, 1e18, "")`.
4. `getAvailableAssetAmount(ETH)` returns `1000e18 - 1000e18 = 0`.
5. `expectedAssetAmount = 1e18 > 0` → transaction reverts with `ExceedAmountToWithdraw`.
6. Bob holds rsETH but cannot enter the withdrawal queue.
7. The operator cannot call `unlockQueue` to reduce `assetsCommitted` until 8 days have elapsed (default `withdrawalDelayBlocks`), because `_unlockWithdrawalRequests` breaks immediately at the delay check (L795).
8. Bob is locked out of the withdrawal queue for up to 8–16 days with no on-chain remedy.

**Foundry test sketch:**
```solidity
function test_initiateWithdrawal_blockedWhenCapacitySaturated() public {
    // Setup: deposit 1000e18 ETH worth of assets
    // Have users A-Z fill assetsCommitted to totalAssets
    // Assert Bob's initiateWithdrawal reverts with ExceedAmountToWithdraw
    // Assert unlockQueue reverts/no-ops before withdrawalDelayBlocks pass
    // Warp forward < withdrawalDelayBlocks, assert still blocked
    // Warp forward >= withdrawalDelayBlocks, operator calls unlockQueue
    // Assert Bob can now call initiateWithdrawal successfully
}
```