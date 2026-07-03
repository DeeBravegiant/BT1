Audit Report

## Title
Unbounded Gas Consumption in Public `updateRSETHPrice()` via N×M Nested Cross-Contract Loops — (File: contracts/LRTOracle.sol)

## Summary
`LRTOracle.updateRSETHPrice()` is a public, permissionless function that executes N×M external calls to EigenLayer's `DelegationManager.getQueuedWithdrawals()` (N = supported assets, M = NodeDelegators), each followed by nested iteration over all queued withdrawals and strategies. As the protocol scales through normal operator activity, this call chain can exceed the block gas limit, permanently preventing price updates and — because the same loop runs inside `_checkIfDepositAmountExceedesCurrentLimit()` and `getAvailableAssetAmount()` — also reverting all user deposits and withdrawal initiations.

## Finding Description

The full call chain is confirmed in the codebase:

**Step 1 — `_getTotalEthInProtocol()` iterates every supported asset** (`LRTOracle.sol` L336–348), calling `getTotalAssetDeposits(asset)` for each.

**Step 2 — `getAssetDistributionData()` iterates every NDC** (`LRTDepositPool.sol` L446–456), calling `INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset)` on each.

**Step 3 — `getAssetUnstaking()` issues one external call to EigenLayer per invocation** (`NodeDelegator.sol` L406–407), then iterates all returned withdrawals and all strategies within each (`NodeDelegator.sol` L409–426).

This produces **N × M** external `getQueuedWithdrawals()` calls per `updateRSETHPrice()` invocation. With 5 assets and 10 NDCs that is 50 external EigenLayer storage reads in a single transaction, each reading up to K withdrawals × L strategies.

The protocol explicitly acknowledges the gas ceiling in `LRTUnstakingVault.sol` L151–153:
> "120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price"

and hard-caps `maxUncompletedWithdrawalCount` at 80. However, this cap:
- bounds the **total withdrawal count** across all NDCs, not the **number of external calls** (N × M);
- does not account for growth in supported assets (no analogous cap exists for asset count);
- does not account for admin raising `maxNodeDelegatorLimit` beyond 10 (`LRTDepositPool.sol` L290–296).

The same `getTotalAssetDeposits()` loop is also invoked on every user deposit via `_checkIfDepositAmountExceedesCurrentLimit()` (`LRTDepositPool.sol` L676–682) and on every withdrawal initiation via `getAvailableAssetAmount()` (`LRTWithdrawalManager.sol` L599–603), meaning gas exhaustion blocks all user-facing protocol entry and exit points.

## Impact Explanation

**Medium — Temporary freezing of funds / Unbounded gas consumption.**

If the cumulative gas cost of the N×M `getQueuedWithdrawals()` call chain exceeds the block gas limit:
1. `updateRSETHPrice()` reverts on every call, making the stored `rsETHPrice` permanently stale.
2. `depositETH()` and `depositAsset()` revert because `_beforeDeposit()` → `_checkIfDepositAmountExceedesCurrentLimit()` → `getTotalAssetDeposits()` executes the same loop.
3. `initiateWithdrawal()` reverts because `getAvailableAssetAmount()` → `getTotalAssetDeposits()` executes the same loop.

This constitutes a temporary (but potentially indefinite) freezing of user funds with no unprivileged recovery path.

## Likelihood Explanation

**Low-Medium.** No attacker action is required. The triggering state — many supported assets, many NDCs, many queued EigenLayer withdrawals — is reached through entirely normal, non-malicious operator activity. The protocol's own comment and the 80-withdrawal hard cap confirm the team is aware the gas ceiling exists. As `maxNodeDelegatorLimit` is raised or more LSTs are added as supported assets, the risk increases monotonically. The `maxUncompletedWithdrawalCount` cap provides partial mitigation but does not bound the number of external calls.

## Recommendation

1. **Decouple TVL accounting from the hot path.** Store a running `totalAssetDeposits` counter updated incrementally on each deposit, withdrawal, and unstaking event rather than recomputing it by iterating all NDCs on every call.
2. **Cache `getQueuedWithdrawals()` results on-chain.** Have a keeper push the unstaking amounts on-chain periodically rather than re-fetching from EigenLayer on every price update.
3. **Add an explicit cap on supported asset count** analogous to `maxNodeDelegatorLimit`, and enforce that `N × M × maxUncompletedWithdrawalCount` stays within a safe gas budget.
4. **Paginate `updateRSETHPrice()`** so that gas cost per transaction is bounded regardless of protocol size.

## Proof of Concept

1. Deploy with 5 supported assets, 10 NDCs (`maxNodeDelegatorLimit = 10`), and 80 total queued EigenLayer withdrawals (8 per NDC), each with 2 strategies.
2. Any unprivileged address calls `LRTOracle.updateRSETHPrice()`.
3. `_getTotalEthInProtocol()` iterates 5 assets; for each, `getAssetDistributionData()` iterates 10 NDCs and calls `getAssetUnstaking()` on each → **50 external calls** to `DelegationManager.getQueuedWithdrawals()`.
4. Each call iterates 8 withdrawals × 2 strategies = 16 inner iterations, totalling 800 EigenLayer storage reads plus all intermediate LRTDepositPool and NodeDelegator reads.
5. A Foundry fork test against mainnet EigenLayer can measure the gas consumed; as `maxNodeDelegatorLimit` is raised or more assets are added, the gas grows proportionally until the transaction reverts with out-of-gas, permanently preventing price updates and blocking all deposits and withdrawal initiations.