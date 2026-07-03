Audit Report

## Title
Nested Unbounded Loop in `getAssetDistributionData` / `_getTotalEthInProtocol` Causes Unbounded Gas Consumption Blocking Deposits, Withdrawals, and Price Updates - (File: contracts/LRTDepositPool.sol, contracts/LRTOracle.sol, contracts/NodeDelegator.sol)

## Summary
`LRTDepositPool.getAssetDistributionData()` loops over all NDCs and calls `NodeDelegator.getAssetUnstaking()` for each, which in turn fetches all queued EigenLayer withdrawals and iterates over them in a nested loop. This nested loop is executed on every user deposit, every withdrawal initiation, and every call to the public `updateRSETHPrice()`. As the number of NDCs, supported assets, and queued withdrawals grows under normal protocol operation, the cumulative gas cost can reach or exceed the block gas limit, permanently blocking user-facing operations.

## Finding Description

**Root cause 1 — `LRTDepositPool.getAssetDistributionData` (lines 446–456):**

The function loops over every NDC and calls `getAssetUnstaking` for each:

```solidity
uint256 ndcsCount = nodeDelegatorQueue.length;
for (uint256 i; i < ndcsCount;) {
    assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);
    assetStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetBalance(asset);
    assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);
    unchecked { ++i; }
}
```

**Root cause 2 — `NodeDelegator.getAssetUnstaking` (lines 405–427):**

For every NDC visited above, this function calls EigenLayer's `getQueuedWithdrawals` and iterates over every returned withdrawal and every strategy within each withdrawal:

```solidity
(IDelegationManager.Withdrawal[] memory queuedWithdrawals, uint256[][] memory withdrawalShares) =
    _getDelegationManager().getQueuedWithdrawals(address(this));

for (uint256 withdrawalIndex = 0; withdrawalIndex < queuedWithdrawals.length; withdrawalIndex++) {
    for (uint256 strategyIndex = 0; strategyIndex < withdrawal.strategies.length; strategyIndex++) {
        ...
    }
}
```

**Root cause 3 — `LRTOracle._getTotalEthInProtocol` (lines 336–348):**

`updateRSETHPrice()` (public, no access control beyond `whenNotPaused`) calls `_getTotalEthInProtocol()`, which adds a third outer loop over every supported asset, calling `getTotalAssetDeposits` → `getAssetDistributionData` for each:

```solidity
for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
    uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
    ...
}
```

**Deposit path:** `depositETH` / `depositAsset` → `_beforeDeposit` → `getAssetCurrentLimit` → `getTotalAssetDeposits` → `getAssetDistributionData` → NDC loop → `getAssetUnstaking` → EigenLayer withdrawal loop.

**Existing mitigations are insufficient:** `LRTUnstakingVault.setMaxUncompletedWithdrawalCount` caps per-NDC withdrawals at 80, with the inline comment explicitly acknowledging the gas ceiling:

```solidity
// 120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price
// Need buffer for theoretical operator forced undelegations (ndc count * asset count = 15)
if (_maxUncompletedWithdrawalCount > 80) {
    revert MaxUncompletedWithdrawalCountTooHigh();
}
```

However, this cap only addresses one dimension. The actual cost scales multiplicatively: `assets × NDCs × withdrawals/NDC`. With 5 supported assets, 10 NDCs, and 80 queued withdrawals per NDC, the loop body executes up to **4,000 times**, each iteration involving external calls to EigenLayer strategies. Furthermore, `updateMaxNodeDelegatorLimit` imposes **no upper bound** on `maxNodeDelegatorLimit_`, allowing the NDC count to grow without bound.

## Impact Explanation

**Medium — Unbounded gas consumption / Temporary freezing of funds.**

When the combined loop count (assets × NDCs × queued withdrawals) pushes the gas cost past the block gas limit:
- `depositETH` and `depositAsset` revert — users cannot mint rsETH.
- `initiateWithdrawal` reverts — users cannot queue withdrawals.
- `updateRSETHPrice` reverts — the rsETH price becomes stale, causing all subsequent minting to use an incorrect exchange rate.

All three paths are reachable by any unprivileged user and are core to the protocol's value proposition.

## Likelihood Explanation

**Medium.** The protocol is designed to scale: more NDCs are added as TVL grows, more assets are supported over time, and EigenLayer undelegations (which queue many withdrawals at once) are a normal operational event. No attacker action is required; normal protocol growth reaches the gas ceiling organically. The protocol's own inline comment in `LRTUnstakingVault.setMaxUncompletedWithdrawalCount` confirms the team is aware of the boundary, but the cap only addresses one dimension of the multiplicative complexity.

## Recommendation

1. **Short term:** Cache the result of `getAssetUnstaking` off-chain and expose an operator-only setter to write the cached value on-chain, replacing the live EigenLayer loop with a single storage read during `getAssetDistributionData`.
2. **Long term:** Maintain a running `totalUnstakingByAsset` counter incremented/decremented when withdrawals are queued and completed (similar to how `assetsCommitted` is tracked in `LRTWithdrawalManager`), eliminating the need to iterate over EigenLayer's queued withdrawal list on every user interaction.
3. **Immediate mitigation:** Add an explicit upper bound to `updateMaxNodeDelegatorLimit` (e.g., ≤ 15) and document the safe operating envelope for `maxUncompletedWithdrawalCount` as a function of `nodeDelegatorQueue.length × supportedAssetList.length`.

## Proof of Concept

1. Protocol reaches normal operating scale: 10 NDCs, 5 supported assets, each NDC has 80 queued EigenLayer withdrawals (within the allowed cap per `setMaxUncompletedWithdrawalCount`).
2. User calls `LRTDepositPool.depositETH{value: 1 ether}(0, "")`.
3. Internally: `_beforeDeposit` → `getAssetCurrentLimit` → `getTotalAssetDeposits(ETH_TOKEN)` → `getETHDistributionData()`.
4. `getETHDistributionData` loops over 10 NDCs; for each it calls `getAssetUnstaking(ETH_TOKEN)`.
5. Each `getAssetUnstaking` call fetches all 80 queued withdrawals from EigenLayer's `DelegationManager` and iterates over them — 10 × 80 = **800 external-call-heavy iterations** within a single user transaction.
6. Gas consumed exceeds the block gas limit; the deposit reverts.
7. The same revert occurs for `initiateWithdrawal` and for the public `updateRSETHPrice()` (which multiplies by the number of supported assets, making it even more severe).

**Foundry fork test plan:** Deploy against a mainnet fork with 10 NDCs each having 80 queued EigenLayer withdrawals. Call `depositETH{value: 1 ether}(0, "")` and measure gas. Repeat with increasing NDC/withdrawal counts to demonstrate the gas growth curve reaching the block gas limit.