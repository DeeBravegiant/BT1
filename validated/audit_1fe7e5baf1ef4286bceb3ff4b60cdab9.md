Audit Report

## Title
Unbounded Nested Loop Gas Consumption in User-Facing Deposit Path - (`contracts/LRTDepositPool.sol`, `contracts/NodeDelegator.sol`)

## Summary
Every call to `depositETH` and `depositAsset` triggers a chain of nested loops: iterating over all NDCs in `nodeDelegatorQueue`, and for each NDC calling `NodeDelegator.getAssetUnstaking()`, which itself loops over all EigenLayer queued withdrawals and their strategies. With no hard upper cap on `maxNodeDelegatorLimit` and unbounded EigenLayer withdrawal counts, this path can exhaust the block gas limit, temporarily freezing all user deposits.

## Finding Description
The full call chain is confirmed in the code:

`depositETH` (L76-93) → `_beforeDeposit` → `getTotalAssetDeposits` (L385-397) → `getAssetDistributionData` / `getETHDistributionData`.

Both distribution functions loop over all NDCs:

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

For each NDC, `getAssetUnstaking` (NodeDelegator.sol L405-427) fetches **all** queued withdrawals from EigenLayer via an external call and iterates with a nested loop:

```solidity
(IDelegationManager.Withdrawal[] memory queuedWithdrawals, ...) =
    _getDelegationManager().getQueuedWithdrawals(address(this));
for (uint256 withdrawalIndex = 0; ...) {
    for (uint256 strategyIndex = 0; ...) { ... }
}
```

`updateMaxNodeDelegatorLimit` (L290-296) enforces only a lower bound (cannot shrink below current queue length) but **no upper bound**, allowing `maxNodeDelegatorLimit` to be set arbitrarily high. As the protocol legitimately adds NDCs and each accumulates queued EigenLayer withdrawals, the total gas cost scales as `O(NDC_count × queued_withdrawals_per_NDC × strategies_per_withdrawal)`, with each inner iteration involving cold EigenLayer storage reads.

Additionally, `updateRSETHPrice()` (LRTOracle.sol L87-89) is publicly callable with no access control, and calls `_getTotalEthInProtocol()` (L331-349), which loops over all supported assets and calls `getTotalAssetDeposits` for each — compounding the loop depth further.

## Impact Explanation
**Medium — Temporary freezing of funds / Unbounded gas consumption.** When the NDC count and per-NDC queued withdrawal count grow to realistic operational levels (e.g., 15 NDCs × 60 withdrawals × 4 strategies = 3,600 cold-storage inner iterations), `depositETH` and `depositAsset` will revert with out-of-gas, preventing any user from depositing until the NDC count or queued withdrawal count is reduced. The same path makes `updateRSETHPrice()` susceptible to out-of-gas, freezing oracle price updates and halting fee minting.

## Likelihood Explanation
The protocol is designed to scale: `maxNodeDelegatorLimit` starts at 10 but is explicitly upgradeable with no ceiling. As TVL grows, more NDCs are operationally expected. Each NDC accumulates queued EigenLayer withdrawals during normal unstaking. This is a foreseeable operational state reachable without any attacker action — normal protocol growth triggers it. Any unprivileged user calling `depositETH`, `depositAsset`, or `updateRSETHPrice()` can trigger the out-of-gas revert once the threshold is crossed.

## Recommendation
1. Enforce a hard upper bound on `maxNodeDelegatorLimit` (e.g., ≤ 10 or ≤ 15) in `updateMaxNodeDelegatorLimit`.
2. Decouple `getAssetUnstaking` from the deposit gas path — maintain a running `assetUnstaking` storage tally updated only when withdrawals are initiated or completed, rather than re-querying EigenLayer on every deposit.
3. Cache total asset distribution in storage (updated lazily) rather than recomputing on every deposit call.
4. Split `getAssetDistributionData` into a view-only function not invoked in state-changing paths.

## Proof of Concept
1. Admin sets `maxNodeDelegatorLimit = 15` and calls `addNodeDelegatorContractToQueue` to add 15 NDCs.
2. Operator initiates unstaking from each NDC across 4 strategies, accumulating 60 queued withdrawals per NDC (within the documented 80-withdrawal limit).
3. User calls `depositETH(0, "")`.
4. Execution: `depositETH` → `_beforeDeposit` → `getTotalAssetDeposits` → `getETHDistributionData` → 15 NDC iterations, each calling `getAssetUnstaking` → 60 withdrawal iterations × 4 strategy iterations = 3,600 inner loop steps with cold EigenLayer storage reads.
5. Transaction reverts with out-of-gas; no user can deposit ETH until NDC count or queued withdrawals are reduced.
6. Separately, any caller invoking `updateRSETHPrice()` hits the same path multiplied by the number of supported assets, also reverting out-of-gas.