Audit Report

## Title
Unbounded Gas Consumption in Publicly Callable `updateRSETHPrice()` via Nested Cross-Contract Loops — (File: contracts/LRTOracle.sol)

## Summary
`LRTOracle.updateRSETHPrice()` is a public, permissionless function that executes a deeply nested loop: for every supported asset it calls `LRTDepositPool.getTotalAssetDeposits()`, which iterates over every NodeDelegator and calls `NodeDelegator.getAssetUnstaking()`, which issues an external call to EigenLayer's `DelegationManager.getQueuedWithdrawals()` and then iterates over every queued withdrawal and every strategy within it. The protocol's own `maxUncompletedWithdrawalCount` cap (hardcoded ceiling of 80) was designed to bound this gas cost, but the cap is a *total* count across all NDCs and does not account for the N×M multiplication of external calls (assets × NDCs), meaning the effective gas budget is consumed N times faster than the protocol's internal analysis assumes.

## Finding Description

**Confirmed call chain:**

`LRTOracle.updateRSETHPrice()` (public, `whenNotPaused`) → `_updateRsETHPrice()` → `_getTotalEthInProtocol()` loops over every supported asset and calls `ILRTDepositPool.getTotalAssetDeposits(asset)` for each. `getTotalAssetDeposits()` calls `getAssetDistributionData()`, which loops over every NDC and calls `INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset)` on each. `getAssetUnstaking()` issues an external call to `DelegationManager.getQueuedWithdrawals(address(this))` and then iterates over all returned withdrawals and all strategies within each withdrawal.

All code references are confirmed:
- `_getTotalEthInProtocol()` asset loop: `LRTOracle.sol` L336–348
- `getAssetDistributionData()` NDC loop with `getAssetUnstaking()` call: `LRTDepositPool.sol` L446–456
- `getAssetUnstaking()` external call + nested loops: `NodeDelegator.sol` L405–427
- Deposit path exposure via `_checkIfDepositAmountExceedesCurrentLimit()` → `getTotalAssetDeposits()`: `LRTDepositPool.sol` L676–682
- Withdrawal path exposure via `getAvailableAssetAmount()` → `getTotalAssetDeposits()`: `LRTWithdrawalManager.sol` L599–603

**Why existing guards are insufficient:**

The protocol acknowledges the gas ceiling in `LRTUnstakingVault.setMaxUncompletedWithdrawalCount()` (L151–153):
> "120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price"

and enforces a hard cap of 80. However, this cap counts *total* withdrawals across all NDCs, not per-NDC. The gas cost of `updateRSETHPrice()` scales as **N × total_withdrawals** (where N = number of supported assets), because `getAssetUnstaking()` is called once per asset per NDC, and each call fetches the full `getQueuedWithdrawals()` result for that NDC. With 5 supported assets and 10 NDCs holding 80 total withdrawals:

- External calls to `getQueuedWithdrawals()`: 5 × 10 = **50**
- Total withdrawal iterations: 5 × 80 = **400**

The protocol's comment appears to have been derived assuming a single asset or a simpler model. With N supported assets, the effective safe withdrawal count is `120 / N`, not 120. At N=5, the safe limit is 24 total withdrawals — well below the enforced cap of 80. Additionally, `maxNodeDelegatorLimit` (initialized to 10) is admin-raisable without an upper bound, further multiplying the gas cost.

## Impact Explanation

**Medium — Unbounded gas consumption / Temporary freezing of funds.**

If `updateRSETHPrice()` reverts with out-of-gas:
- The stored `rsETHPrice` becomes stale, causing systematic mis-minting on all subsequent deposits.
- If `getAssetDistributionData()` itself exceeds the block gas limit, `depositETH()`, `depositAsset()`, and `initiateWithdrawal()` all revert (they call `getTotalAssetDeposits()` on the hot path), temporarily freezing user access to the protocol.

## Likelihood Explanation

The triggering state — many supported assets, many NDCs, many queued EigenLayer withdrawals — is reached through entirely normal, non-malicious operator activity. No attacker action is required. The protocol's own comment confirms awareness of the gas ceiling. As the protocol adds more LSTs and more NodeDelegators, the risk increases monotonically. The `maxUncompletedWithdrawalCount` cap of 80 is already above the corrected safe limit of `120 / N_assets`, meaning the condition can be reached within the protocol's own enforced parameters.

## Recommendation

1. **Fix the cap formula**: enforce `maxUncompletedWithdrawalCount ≤ floor(120 / supportedAssetCount)` so the total gas budget remains safe regardless of the number of supported assets.
2. **Decouple TVL accounting from the hot path**: maintain a running `totalAssetDeposits` counter updated incrementally on each deposit/withdrawal/unstaking event rather than recomputing by iterating all NDCs on every call.
3. **Cache `getQueuedWithdrawals()` results**: push queued withdrawal data on-chain via a keeper rather than re-fetching from EigenLayer on every price update.
4. **Add an explicit cap on supported assets** analogous to `maxNodeDelegatorLimit`, and enforce that `N_assets × N_NDCs × maxUncompletedWithdrawalCount` stays within a safe gas budget.

## Proof of Concept

1. Protocol has 5 supported assets, 10 NDCs, and 80 total queued EigenLayer withdrawals (8 per NDC on average), each with 2 strategies — all within the protocol's own enforced caps.
2. Anyone calls `LRTOracle.updateRSETHPrice()`.
3. `_getTotalEthInProtocol()` iterates 5 assets × 10 NDCs = **50 external calls** to `DelegationManager.getQueuedWithdrawals()`, each reading EigenLayer storage for 8 withdrawals × 2 strategies = 16 iterations.
4. Total EigenLayer storage iterations: 50 × 16 = **800**, plus all intermediate `LRTDepositPool` and `NodeDelegator` storage reads.
5. The protocol's own safe limit of 120 total withdrawals was derived without accounting for the N=5 asset multiplier; the corrected safe limit is 24 total withdrawals. With 80 total withdrawals enforced by the cap, the transaction consumes ~3.3× the intended gas budget.
6. A Foundry fork test against mainnet EigenLayer can confirm the gas cost by calling `updateRSETHPrice()` with the above state and measuring gas consumption against the 30M block gas limit.