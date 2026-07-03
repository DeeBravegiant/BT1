Audit Report

## Title
Unbounded Gas Consumption in `getAssetUnstaking()` Called on Every Deposit and Withdrawal Initiation - (File: contracts/NodeDelegator.sol)

## Summary

`NodeDelegator.getAssetUnstaking()` fetches all queued EigenLayer withdrawals via an external call and iterates over them with a nested loop on every invocation. This function is called on every user deposit (`depositETH`, `depositAsset`) and every withdrawal initiation (`initiateWithdrawal`) through the TVL accounting chain, causing gas costs to scale proportionally with the number of pending unstaking operations across all NodeDelegators. The protocol itself acknowledges this as a real operational constraint via the `maxUncompletedWithdrawalCount` cap.

## Finding Description

**Root cause:** `getAssetUnstaking()` in `NodeDelegator.sol` (L405–427) makes an external call to `DelegationManager.getQueuedWithdrawals()` and then iterates with a nested loop — outer over all queued withdrawals, inner over all strategies per withdrawal — on every call. There is no caching; the full iteration is repeated each time.

**Call chain from deposits:**
`depositETH()` / `depositAsset()` (L76–118) → `_beforeDeposit()` → `_checkIfDepositAmountExceedesCurrentLimit()` (L676–682) → `getTotalAssetDeposits()` → `getAssetDistributionData()` / `getETHDistributionData()` (L446–456, L484–493) → for each NDC in `nodeDelegatorQueue`: `getAssetUnstaking(asset)`.

**Call chain from withdrawal initiation:**
`initiateWithdrawal()` (L168–170) → `getAvailableAssetAmount()` (L599–603) → `lrtDepositPool.getTotalAssetDeposits(asset)` → same chain above.

**Call chain from price update:**
`updateRSETHPrice()` (L87–89) → `_getTotalEthInProtocol()` (L331–349) → for each supported asset: `getTotalAssetDeposits(asset)` → same chain, multiplied by asset count.

**Protocol acknowledgment:** The comment in `setMaxUncompletedWithdrawalCount()` (L151) explicitly states: *"120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price"* — confirming the loop cost is a real operational constraint. The cap is set at 80 (L153), but this is a shared counter across all NDCs. With up to `maxNodeDelegatorLimit = 10` NDCs (L49), each deposit executes up to `NDC_count × queued_withdrawals_per_NDC × strategies_per_withdrawal` iterations plus external calls, all within a single user transaction.

**Existing guards insufficient:** The `maxUncompletedWithdrawalCount` cap bounds the total number of queued withdrawals but does not reduce the per-transaction iteration cost — it merely prevents the worst case from growing further. The cap is an operational workaround, not a code-level fix to the O(N) read pattern on every user-facing call.

## Impact Explanation

**Medium. Unbounded gas consumption.** Gas cost of every deposit and withdrawal initiation scales linearly with the number of pending EigenLayer unstaking operations across all NDCs. At or near the 80-withdrawal cap, user-facing transactions execute the maximum iteration depth on every call. The protocol's own comment confirms that 120 total withdrawals would break `updateRSETHPrice`, meaning the margin between the cap (80) and the breaking point is narrow. Any forced undelegations (acknowledged in the comment: "ndc count * asset count = 15") can push the effective count toward the breaking point, causing deposits and withdrawal initiations to revert — constituting temporary freezing of funds for all users.

## Likelihood Explanation

During periods of high withdrawal demand — a normal operational scenario — operators queue many `initiateUnstaking()` calls. The protocol is explicitly designed to operate with up to 80 uncompleted withdrawals. At this level, every deposit and withdrawal initiation by any unprivileged user executes the nested loop at near-maximum depth. No attacker capability is required; this is a realistic steady-state condition triggered by normal user actions (`depositETH`, `depositAsset`, `initiateWithdrawal`).

## Recommendation

Cache the `assetUnstaking` value in storage and update it incrementally only when withdrawals are queued (in `initiateUnstaking()`) or completed (in `completeUnstaking()`), rather than recomputing it by iterating over all EigenLayer queued withdrawals on every read. This eliminates the O(N) external call chain from user-facing deposit and withdrawal paths, replacing it with an O(1) storage read.

## Proof of Concept

1. Operators call `initiateUnstaking()` on multiple NDCs until `uncompletedWithdrawalCount` approaches 80.
2. Any unprivileged user calls `depositETH(1 ether, "")`.
3. The call chain executes: `depositETH` → `_beforeDeposit` → `_checkIfDepositAmountExceedesCurrentLimit` → `getTotalAssetDeposits` → `getETHDistributionData` → for each of N NDCs: `getAssetUnstaking(ETH_TOKEN)` → `getQueuedWithdrawals()` + nested loop over all queued withdrawals and strategies.
4. With 10 NDCs each holding 8 queued withdrawals of 3 strategies each, the inner loop executes 240 iterations plus 10 external `getQueuedWithdrawals()` calls within a single deposit transaction.
5. Gas cost grows proportionally; at the cap, the transaction approaches or exceeds the block gas limit, causing the deposit to revert.

**Foundry fork test plan:**
- Fork mainnet with EigenLayer contracts deployed.
- Deploy the protocol, register 10 NDCs, queue 8 withdrawals per NDC via `initiateUnstaking()`.
- Call `depositETH{value: 1 ether}(0, "")` and measure gas via `vm.expectCall` + `gasleft()` snapshots.
- Assert gas consumption scales linearly with withdrawal count and approaches block gas limit at the cap.