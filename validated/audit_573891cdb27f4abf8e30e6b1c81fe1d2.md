Audit Report

## Title
Unbounded Nested Loops in `getAssetDistributionData` and `_getTotalEthInProtocol` Cause OOG, Temporarily Freezing Deposits and Price Updates - (File: contracts/LRTDepositPool.sol, contracts/LRTOracle.sol)

## Summary
`LRTDepositPool.getAssetDistributionData()` and `getETHDistributionData()` loop over every NDC in `nodeDelegatorQueue` and call `NodeDelegator.getAssetUnstaking()` per NDC, which itself executes a nested loop over all EigenLayer queued withdrawals and their strategies. As `queuedWithdrawals` grows through routine `initiateUnstaking()` operations, the cumulative gas cost of these nested loops can exceed the block gas limit, causing `depositETH()`/`depositAsset()` and the public `updateRSETHPrice()` to revert with out-of-gas, temporarily freezing user deposits and staling the rsETH price oracle.

## Finding Description

**Deposit path:**
`depositETH()`/`depositAsset()` → `_beforeDeposit()` → `_checkIfDepositAmountExceedesCurrentLimit()` → `getTotalAssetDeposits()` → `getAssetDistributionData()`.

Inside `getAssetDistributionData()`, a loop iterates over every NDC and calls `getAssetUnstaking()` on each:

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

`getAssetUnstaking()` in `NodeDelegator.sol` fetches all queued withdrawals live from EigenLayer's `DelegationManager` and iterates over them with a nested strategy loop:

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

The `queuedWithdrawals` array is unbounded — it grows with every `initiateUnstaking()` call and shrinks only when `completeUnstaking()` is called. There is no cap on how many withdrawals can be queued per NDC.

**Price update path:**
`updateRSETHPrice()` (public, only `whenNotPaused`) → `_updateRsETHPrice()` → `_getTotalEthInProtocol()`:

```solidity
// LRTOracle.sol L336-348
for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
    uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
    ...
}
```

`getTotalAssetDeposits()` calls `getAssetDistributionData()` for every supported asset, triggering the full NDC × queued-withdrawal nested loop for each asset. This creates a triple-nested loop: `supportedAssets.length × nodeDelegatorQueue.length × queuedWithdrawals.length × strategies.length`.

**Existing guards are insufficient:** `maxNodeDelegatorLimit` is initialized to 10 and is admin-adjustable, but even at 10 NDCs with a realistic number of queued withdrawals per NDC, the gas cost becomes prohibitive. There is no cap on `queuedWithdrawals.length` at the EigenLayer level, and no pagination or caching mechanism in the view functions.

## Impact Explanation

**Medium — Unbounded gas consumption / Temporary freezing of funds.**

When the nested loop gas cost exceeds the block gas limit, every call to `depositETH()` and `depositAsset()` reverts, preventing users from depositing into the protocol. Simultaneously, `updateRSETHPrice()` reverts, leaving `rsETHPrice` stale, which causes mispricing of new deposits and prevents protocol fee minting. Both impacts are temporary (until queued withdrawals are completed) but can persist for extended periods during high unstaking activity.

## Likelihood Explanation

The `queuedWithdrawals` array grows through routine operator operations (`initiateUnstaking()`) — not through any malicious action. During validator exits or operator undelegation events, many withdrawals accumulate simultaneously across multiple NDCs. With `maxNodeDelegatorLimit = 10` NDCs and even 20–30 queued withdrawals per NDC with 2 strategies each, the gas cost of the nested loops across all supported assets becomes prohibitive. This is a realistic operational scenario that any unprivileged user can trigger by calling `depositETH()` or `updateRSETHPrice()` once the state has accumulated.

## Recommendation

1. **Cache `getAssetUnstaking` off-chain**: Store a cached `assetUnstaking` value updated by the operator via a dedicated write function, rather than computing it live on every deposit and price-update call.
2. **Paginate `getAssetDistributionData`**: Accept `from`/`to` index parameters for the NDC loop to allow off-chain aggregation.
3. **Decouple price update from full TVL scan**: Store per-asset TVL snapshots updated incrementally rather than recomputing the full sum on every `updateRSETHPrice()` call.

## Proof of Concept

1. Protocol has 3 supported assets and 10 NDCs (`nodeDelegatorQueue.length = 10`, within the default `maxNodeDelegatorLimit`).
2. Operator calls `initiateUnstaking()` repeatedly over time; each NDC accumulates 30 queued withdrawals, each with 2 strategies → 60 strategy iterations per NDC.
3. A user calls `depositETH(...)`.
4. Execution: `_beforeDeposit` → `_checkIfDepositAmountExceedesCurrentLimit` → `getTotalAssetDeposits(ETH)` → `getETHDistributionData()` → loop over 10 NDCs → each NDC calls `getAssetUnstaking(ETH)` → fetches 30 withdrawals × 2 strategies = 60 iterations per NDC → 600 total inner iterations, each with external `DelegationManager` storage reads.
5. For `updateRSETHPrice()`: the outer loop runs for 3 assets, each triggering the same 600-iteration inner loop → 1800 total inner iterations plus 3 × 10 = 30 `getAssetBalance()` external calls.
6. Both transactions revert with out-of-gas.

**Foundry fork test plan:**
- Fork mainnet/testnet with a deployed EigenLayer `DelegationManager`.
- Deploy 10 NDCs and register them in `nodeDelegatorQueue`.
- For each NDC, call `initiateUnstaking()` 30 times to populate `queuedWithdrawals`.
- Call `depositETH{value: 1 ether}(0, "")` and assert it reverts with out-of-gas, or measure gas consumption exceeding 30M gas.
- Call `updateRSETHPrice()` and assert the same.