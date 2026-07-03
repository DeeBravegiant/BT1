Audit Report

## Title
Blocked User Bypasses rsETH Transfer Restriction via Pre-Initiated Withdrawal Completion — (File: contracts/LRTWithdrawalManager.sol)

## Summary
`RSETH._enforceNotBlocked` is enforced on `_transfer`, `mint`, and `burnFrom`, but `LRTWithdrawalManager._processWithdrawalCompletion` disburses ETH or LST to the user without any blocked-user check. A user who queued a withdrawal before being blocked can call `completeWithdrawal` while blocked and receive their underlying assets, defeating the protocol's freeze mechanism entirely. The admin's `recoverFrozenFunds` cannot intercept this because the rsETH was already moved out of the user's wallet at `initiateWithdrawal` time.

## Finding Description
`RSETH._enforceNotBlocked` is called in three places:
- `mint` (L238): checks recipient before minting
- `burnFrom` (L246): checks account before burning
- `_transfer` (L288–289): checks both `from` and `to`

When `initiateWithdrawal` is called, `safeTransferFrom` moves rsETH from the user to `LRTWithdrawalManager` (L166), triggering `RSETH._transfer` → `_enforceNotBlocked(msg.sender)`. A currently-blocked user cannot initiate a new withdrawal. However, if the user initiated before being blocked, the rsETH is already held by `LRTWithdrawalManager`.

Later, `unlockQueue` burns rsETH from `LRTWithdrawalManager` itself (L305):
```solidity
if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
```
This calls `burnFrom(address(LRTWithdrawalManager), ...)`, which checks `_enforceNotBlocked(LRTWithdrawalManager)` — not the user. The user's block status is irrelevant here.

Finally, `_processWithdrawalCompletion` (L699–738) calls `_transferAsset(asset, user, request.expectedAssetAmount)` at L734 with no blocked-user check. `_transferAsset` (L876–883) sends native ETH via `.call` or LST via `safeTransfer` — neither path touches `RSETH._enforceNotBlocked`.

`recoverFrozenFunds` (L215–218) can only recover rsETH held at the blocked address:
```solidity
uint256 accountBalance = balanceOf(from);
super._transfer(from, custodyAddress, accountBalance);
```
Since the rsETH was moved to `LRTWithdrawalManager` before the block was applied, `balanceOf(user)` is zero and nothing is recovered.

Exploit path:
1. Alice calls `initiateWithdrawal(ETH, amount, "")` — rsETH transferred to `LRTWithdrawalManager`
2. Admin calls `blockUserTransfers([Alice])` — `transfersBlockedUntil[Alice] = block.timestamp + 1 days`
3. Admin calls `recoverFrozenFunds(Alice)` — `balanceOf(Alice) == 0`, nothing recovered
4. Operator calls `unlockQueue` — rsETH burned from `LRTWithdrawalManager`, not Alice
5. After `withdrawalDelayBlocks` (~8 days), Alice calls `completeWithdrawal(ETH, "")` — `_processWithdrawalCompletion` performs no block check and sends ETH to Alice

## Impact Explanation
**Low. Contract fails to deliver promised returns, but doesn't lose value.**

The protocol's blocking mechanism promises to freeze a flagged user's economic position. A user who queued a withdrawal before being blocked can fully exit the protocol via `completeWithdrawal`, receiving their underlying ETH or LST. No third-party funds are stolen and no value is destroyed — the user recovers only their own assets — but the protocol's compliance/freeze guarantee is not upheld. This does not meet the bar for "Medium. Temporary freezing of funds" (which requires funds to be inaccessible when they should be accessible); here the opposite occurs: funds escape a freeze that should have held.

## Likelihood Explanation
The standard `withdrawalDelayBlocks` is `8 days / 12 seconds` (~57,600 blocks), creating a realistic window during which a compliance event could trigger a block after `initiateWithdrawal`. No special permissions, front-running, or victim mistakes are required. The user simply waits for the delay to pass and calls `completeWithdrawal`. The admin can refresh the 24-hour block repeatedly, but `_processWithdrawalCompletion` never consults it. Any user flagged after initiating a withdrawal can exploit this gap.

## Recommendation
Add a blocked-user check inside `_processWithdrawalCompletion` before calling `_transferAsset`:

```solidity
IRSETH rseth = IRSETH(lrtConfig.rsETH());
uint256 blockedUntil = rseth.transfersBlockedUntil(user);
if (blockedUntil != 0 && block.timestamp < blockedUntil && !rseth.isPermanentlyExempt(user)) {
    revert UserTransfersBlocked(user);
}
```

Alternatively, expose an `isBlocked(address)` view on `RSETH` and call it here, mirroring the pattern already used in `mint` and `burnFrom`.

## Proof of Concept
Foundry fork test outline:
1. Deploy/fork with `LRTWithdrawalManager` and `RSETH` configured.
2. Alice holds rsETH; call `initiateWithdrawal(ETH, amount, "")` — confirm rsETH transferred to `LRTWithdrawalManager`.
3. LRTManager calls `blockUserTransfers([Alice])` — confirm `transfersBlockedUntil[Alice] > block.timestamp`.
4. LRTAdmin calls `recoverFrozenFunds(Alice)` — assert emitted `FrozenFundsRecovered` with `amount == 0`.
5. Operator calls `unlockQueue(ETH, ...)` — rsETH burned from `LRTWithdrawalManager`.
6. `vm.roll(block.number + withdrawalDelayBlocks + 1)` to pass the delay.
7. Alice calls `completeWithdrawal(ETH, "")` — assert call succeeds and Alice's ETH balance increases by `expectedAssetAmount`, confirming the block was not enforced.