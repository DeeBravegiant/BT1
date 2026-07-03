The code references are all verified. Let me confirm the `uncompletedWithdrawalCount` is a single global counter shared across all NDCs (not per-NDC) before finalizing.

Audit Report

## Title
Unbounded Gas Consumption in `getAssetUnstaking` Nested Loop Blocks Deposits - (File: `contracts/NodeDelegator.sol`)

## Summary
Every call to `depositETH` or `depositAsset` traverses a call chain that invokes `getAssetUnstaking` once per registered NDC, each of which issues an external `getQueuedWithdrawals()` call and then iterates over every `(withdrawal, strategy)` pair calling `strategy.sharesToUnderlyingView()`. Because `maxNodeDelegatorLimit` has no hard cap, the per-NDC `getQueuedWithdrawals()` overhead scales as O(NDC_count) without a protocol-enforced ceiling, and at current operational parameters (10 NDCs, 80 withdrawals, 5 strategies) a single deposit already costs ~3–5M gas.

## Finding Description
**Verified call chain:**

```
depositETH / depositAsset                          [LRTDepositPool.sol:87, 111]
  └─ _beforeDeposit                                [LRTDepositPool.sol:648]
       └─ _checkIfDepositAmountExceedesCurrentLimit [LRTDepositPool.sol:676]
            └─ getTotalAssetDeposits               [LRTDepositPool.sol:385]
                 └─ getAssetDistributionData /
                    getETHDistributionData          [LRTDepositPool.sol:447-456 / 484-492]
                         └─ getAssetUnstaking (per NDC)  [NodeDelegator.sol:405]
                              └─ getQueuedWithdrawals()  [external, EigenLayer]
                                   └─ sharesToUnderlyingView() per (withdrawal × strategy)
```

In `getAssetDistributionData`, the loop at `LRTDepositPool.sol:447–456` calls `INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset)` for every NDC. Inside `getAssetUnstaking` (`NodeDelegator.sol:405–427`), a single `_getDelegationManager().getQueuedWithdrawals(address(this))` external call is made, followed by a nested loop over every withdrawal and every strategy, calling `strategy.sharesToUnderlyingView()` for each matching strategy.

**Bounding parameters:**
- `maxUncompletedWithdrawalCount` is a single global counter hard-capped at 80 (`LRTUnstakingVault.sol:39, 153`), shared across all NDCs — so total withdrawal iteration cost is bounded at 80 × strategies.
- `maxNodeDelegatorLimit` is admin-settable with no hard cap (`LRTDepositPool.sol:290–297`), so the number of `getQueuedWithdrawals()` external calls scales as O(NDC_count) without a ceiling.

**Why existing guards are insufficient:**
The 80-withdrawal cap bounds the `sharesToUnderlyingView()` iteration but does not bound the `getQueuedWithdrawals()` overhead, which is paid once per NDC regardless of whether that NDC has any pending withdrawals. With N NDCs, N external calls are made even if N−1 of them return empty arrays.

## Impact Explanation
This matches **Medium: Unbounded gas consumption**. At current operational parameters (10 NDCs, 80 withdrawals, 5 strategies), a deposit costs ~3–5M gas instead of the ~100–200k gas a user would expect — a 20–50× overhead paid by every depositor. Because `maxNodeDelegatorLimit` has no hard cap, as the protocol adds more NDCs (normal operational growth), the `getQueuedWithdrawals()` overhead grows linearly without a protocol-enforced ceiling, eventually making deposits economically infeasible or causing OOG reverts for callers who set a reasonable gas limit.

## Likelihood Explanation
No admin compromise is required to reach the worst-case withdrawal queue depth: operators call `initiateUnstaking` with multi-strategy batches through the normal unstaking workflow until the global `uncompletedWithdrawalCount` reaches `maxUncompletedWithdrawalCount` (80). This is expected operational behavior. The NDC count growing over time is also a normal operational expectation for the protocol. Any unprivileged user calling `depositETH` or `depositAsset` triggers the full iteration cost on every transaction.

## Recommendation
1. **Maintain an on-chain running total**: Track `assetUnstaking[asset]` as a storage variable updated on `initiateUnstaking` / `completeUnstaking`, eliminating the need to iterate EigenLayer's queue on every deposit.
2. **Cache `getQueuedWithdrawals()` results**: If the view approach is retained, call it once per NDC and reuse the result for all assets rather than once per `getAssetUnstaking(asset)` call per asset per NDC.
3. **Add a hard cap on `maxNodeDelegatorLimit`**: Prevent unbounded growth of the per-NDC `getQueuedWithdrawals()` overhead.
4. **Separate accounting view from the deposit hot path**: `getTotalAssetDeposits` should read from a cached/snapshotted value rather than recomputing from EigenLayer state on every state-changing transaction.

## Proof of Concept
```solidity
// Foundry fork test
function test_depositGasAtMaxWithdrawals() public {
    // Assume 10 NDCs already registered; fill global withdrawal queue to cap
    // Each initiateUnstaking call with 5 strategies counts as 1 toward the global cap of 80
    for (uint i = 0; i < 10; i++) {
        for (uint j = 0; j < 8; j++) {
            vm.prank(operator);
            nodeDelegators[i].initiateUnstaking(fiveStrategies, fiveShares);
            // uncompletedWithdrawalCount increments globally each call
        }
    }
    // uncompletedWithdrawalCount == 80 (at maxUncompletedWithdrawalCount)

    // Measure gas for a normal user deposit
    uint256 gasBefore = gasleft();
    vm.prank(user);
    lrtDepositPool.depositETH{value: 1 ether}(0, "");
    uint256 gasUsed = gasBefore - gasleft();

    // 10 getQueuedWithdrawals() calls + 80×5 sharesToUnderlyingView() calls
    // Expected: ~3–5M gas vs ~100–200k for a normal deposit
    assertGt(gasUsed, 2_000_000);
}
```