Audit Report

## Title
Unbounded Nested Loops via `getAssetUnstaking` in Deposit and Price-Update Paths Cause OOG - (File: contracts/LRTDepositPool.sol, contracts/NodeDelegator.sol, contracts/LRTOracle.sol)

## Summary
`getAssetDistributionData()` and `getETHDistributionData()` loop over every NDC in `nodeDelegatorQueue` and call `INodeDelegator.getAssetUnstaking()` on each, which itself executes a nested loop over all queued EigenLayer withdrawals and their strategies fetched live from `DelegationManager.getQueuedWithdrawals()`. Because the queued-withdrawal array is unbounded and grows with every routine `initiateUnstaking()` call, the gas cost of both the user deposit path and the public `updateRSETHPrice()` path scales without bound, eventually causing OOG reverts that freeze deposits and stale the rsETH price oracle.

## Finding Description
**Confirmed call chain — deposit path:**

`depositETH()` / `depositAsset()` → `_beforeDeposit()` → `_checkIfDepositAmountExceedesCurrentLimit()` → `getTotalAssetDeposits()` → `getAssetDistributionData()` / `getETHDistributionData()`.

Inside both distribution functions, a loop iterates over every NDC:

```solidity
// LRTDepositPool.sol L446-456
uint256 ndcsCount = nodeDelegatorQueue.length;
for (uint256 i; i < ndcsCount;) {
    assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);
    assetStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetBalance(asset);
    assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);
    unchecked { ++i; }
}
```

Each `getAssetUnstaking()` call fetches the full `queuedWithdrawals` array from EigenLayer and iterates over it with a nested strategy loop:

```solidity
// NodeDelegator.sol L405-427
(IDelegationManager.Withdrawal[] memory queuedWithdrawals, ...) =
    _getDelegationManager().getQueuedWithdrawals(address(this));
for (uint256 withdrawalIndex = 0; withdrawalIndex < queuedWithdrawals.length; withdrawalIndex++) {
    for (uint256 strategyIndex = 0; strategyIndex < withdrawal.strategies.length; strategyIndex++) {
        ...
    }
}
```

**Confirmed call chain — price update path:**

`updateRSETHPrice()` (public, only `whenNotPaused`) → `_updateRsETHPrice()` → `_getTotalEthInProtocol()`:

```solidity
// LRTOracle.sol L336-348
for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
    uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
    ...
}
```

This creates a triple-nested loop: `supportedAssets × nodeDelegatorQueue × queuedWithdrawals × strategies`, each iteration performing external storage reads against EigenLayer's `DelegationManager`.

**Why existing guards are insufficient:**

`maxNodeDelegatorLimit` is initialized to 10 and bounds the outer NDC loop, but there is no cap on the number of queued withdrawals per NDC. The `queuedWithdrawals` array grows with every `initiateUnstaking()` call (a routine operator operation) and shrinks only when `completeUnstaking()` is called. During the 7-day EigenLayer withdrawal delay window, withdrawals accumulate freely. No pagination, caching, or gas guard exists on any of these loops.

## Impact Explanation
**Medium — Unbounded gas consumption / Temporary freezing of funds.**

When accumulated queued withdrawals cause the nested loop to exceed the block gas limit, every call to `depositETH()` and `depositAsset()` reverts, preventing users from depositing. Simultaneously, `updateRSETHPrice()` reverts, leaving `rsETHPrice` stale and causing mispricing of new deposits and withdrawals. Both impacts are temporary (until withdrawals are completed) but can persist for the full 7-day EigenLayer withdrawal delay window.

## Likelihood Explanation
The precondition — many accumulated queued withdrawals — arises from normal operator activity, not from any attacker action. With `maxNodeDelegatorLimit = 10` NDCs and 30–50 queued withdrawals per NDC (each with 2 strategies), the inner loop executes 600–1000 iterations of external storage reads per transaction, which is sufficient to approach or exceed the 30M block gas limit. This is a realistic operational scenario during periods of high validator exits or operator undelegation. Any unprivileged user can trigger the OOG by calling `depositETH()` or `updateRSETHPrice()` when this state exists.

## Recommendation
1. **Cache `assetUnstaking` off-chain**: Store a per-NDC per-asset `assetUnstaking` value updated by the operator via a privileged setter, rather than computing it live from EigenLayer on every deposit and price-update call.
2. **Paginate NDC iteration**: Accept `from`/`to` index parameters in `getAssetDistributionData` and aggregate results off-chain.
3. **Decouple price update from full TVL scan**: Store per-asset TVL snapshots updated incrementally rather than recomputing the full sum on every `updateRSETHPrice()` call.

## Proof of Concept
1. Protocol has 3 supported assets, 5 NDCs (`nodeDelegatorQueue.length = 5`).
2. Operator calls `initiateUnstaking()` repeatedly; each NDC accumulates 50 queued withdrawals with 2 strategies each.
3. User calls `depositETH(minRSETHAmountExpected, referralId)`.
4. Execution: `_beforeDeposit` → `_checkIfDepositAmountExceedesCurrentLimit` → `getTotalAssetDeposits(ETH)` → `getETHDistributionData()` → loop over 5 NDCs → each NDC calls `getAssetUnstaking(ETH)` → `getQueuedWithdrawals()` returns 50 withdrawals × 2 strategies = 100 inner iterations per NDC → 500 total inner iterations, each with multiple external `DelegationManager` storage reads.
5. For `updateRSETHPrice()`: outer loop runs for 3 assets, each triggering the same 500-iteration inner loop → 1500 total inner iterations plus 15 `getAssetBalance()` external calls.
6. Both transactions revert with out-of-gas. A Foundry fork test against mainnet EigenLayer with a mock NDC that returns a large `queuedWithdrawals` array can reproduce the OOG revert deterministically.