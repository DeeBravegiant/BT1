Audit Report

## Title
Unbounded Nested Loops in `_getTotalEthInProtocol()` Can Cause Permanent Oracle and Deposit Freeze - (File: contracts/LRTOracle.sol)

## Summary
`LRTOracle.updateRSETHPrice()` is publicly callable and internally executes nested loops over `supportedAssetList` and `nodeDelegatorQueue`, with no on-chain hard cap enforced on either array's length. As the protocol grows legitimately, this function can exceed the block gas limit, permanently freezing the price oracle and halting all deposits. The protocol has partially mitigated the innermost withdrawal loop via `maxUncompletedWithdrawalCount <= 80`, but the outer loops over assets and NDCs remain unbounded.

## Finding Description
`updateRSETHPrice()` (only `whenNotPaused`, callable by anyone) invokes `_updateRsETHPrice()` → `_getTotalEthInProtocol()`, which iterates over every supported asset:

```solidity
// LRTOracle.sol L336-349
for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
    uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
    ...
}
```

`getTotalAssetDeposits(asset)` → `getAssetDistributionData(asset)` iterates over every NDC in `nodeDelegatorQueue`:

```solidity
// LRTDepositPool.sol L446-456
for (uint256 i; i < ndcsCount;) {
    assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);
    assetStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetBalance(asset);
    assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);
    ...
}
```

`getAssetUnstaking(asset)` calls `getQueuedWithdrawals()` (one external call per NDC per asset) and then iterates over returned withdrawals and their strategies.

**Existing mitigations and their insufficiency:**

`LRTUnstakingVault.setMaxUncompletedWithdrawalCount()` enforces a hard cap of 80 on total queued withdrawals across all NDCs, which bounds the innermost withdrawal iteration. The comment explicitly acknowledges this was designed to keep `updateRSETHPrice` callable. However, this cap does **not** bound the outer two loops:

- `supportedAssetList` grows via `_addNewSupportedAsset()` with no maximum enforced.
- `maxNodeDelegatorLimit` is admin-settable via `updateMaxNodeDelegatorLimit()` with no upper bound; it initializes to 10 but can be raised arbitrarily.

The total external call count scales as `supportedAssets.length × nodeDelegatorQueue.length × 3` (one `balanceOf`, one `getAssetBalance`, one `getAssetUnstaking` per NDC per asset), plus one `getQueuedWithdrawals` per NDC per asset. At 15 assets × 50 NDCs, this is ~2,250 external calls before any withdrawal iteration, each costing thousands of gas, approaching the 30M block gas limit. The same nested loop is also triggered on every user deposit via `_beforeDeposit()` → `_checkIfDepositAmountExceedesCurrentLimit()` → `getTotalAssetDeposits()`.

## Impact Explanation
If `updateRSETHPrice()` reverts due to out-of-gas, `rsETHPrice` is never updated. All deposits rely on `getRsETHAmountToMint()` → `lrtOracle.rsETHPrice()`, and all withdrawals rely on `getExpectedAssetAmount()` → `lrtOracle.rsETHPrice()`. A permanently stale oracle effectively halts the entire protocol. This constitutes **temporary (escalating to permanent) freezing of funds** — Medium severity, matching the allowed impact scope.

## Likelihood Explanation
The protocol is designed to grow: more LSTs are added as supported assets via `TIME_LOCK_ROLE`, more NDCs are deployed by admin to scale ETH restaking. These are normal protocol operations, not attacks. Once the gas threshold is crossed, any unprivileged user calling `updateRSETHPrice()` triggers the revert. The `maxUncompletedWithdrawalCount <= 80` cap demonstrates the developers are aware of gas constraints but have only partially addressed them — the outer loops remain uncapped.

## Recommendation
1. Enforce an on-chain maximum for `supportedAssetList.length` in `_addNewSupportedAsset()` (e.g., `require(supportedAssetList.length < MAX_SUPPORTED_ASSETS)`).
2. Enforce an on-chain hard cap for `maxNodeDelegatorLimit` in `updateMaxNodeDelegatorLimit()` (e.g., `require(maxNodeDelegatorLimit_ <= MAX_NDC_COUNT)`).
3. Consider caching per-asset TVL snapshots updated lazily rather than recomputing the full sum on every price update, or paginating `_getTotalEthInProtocol()`.
4. Add a gas-cost benchmark test that fails if the combined loop gas exceeds a safe fraction of the block gas limit.

## Proof of Concept
1. Protocol adds 15 supported LST assets via `addNewSupportedAsset()` (TIME_LOCK_ROLE — normal protocol growth).
2. Admin sets `maxNodeDelegatorLimit` to 50 and adds 50 NDCs (normal protocol scaling).
3. `maxUncompletedWithdrawalCount` is set to 80 (its maximum allowed value); 80 queued withdrawals exist across NDCs.
4. Any user calls `updateRSETHPrice()`.
5. `_getTotalEthInProtocol()` executes: 15 assets × 50 NDCs × (1 `balanceOf` + 1 `getAssetBalance` + 1 `getAssetUnstaking` with 1 `getQueuedWithdrawals` each) = ~2,250 external calls + 80 withdrawal iterations. Each external call costs ~2,100–5,000+ gas cold; total approaches or exceeds the 30M block gas limit.
6. Transaction reverts with out-of-gas; `rsETHPrice` is never updated; all subsequent deposits and withdrawals revert at the oracle price fetch.

**Foundry test plan:** Deploy mock NDCs and mock EigenLayer delegation manager returning configurable queued withdrawal arrays. Parameterize `supportedAssets.length` and `nodeDelegatorQueue.length`. Use `vm.gasleft()` before and after `updateRSETHPrice()` to measure consumption. Assert that at (15 assets, 50 NDCs, 80 withdrawals) the call reverts with out-of-gas on a 30M gas limit fork.