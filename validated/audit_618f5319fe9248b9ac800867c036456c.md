Audit Report

## Title
Asymmetric Aave Error Handling Causes Temporary ETH Withdrawal Freeze After rsETH Is Burned — (File: `contracts/LRTWithdrawalManager.sol`)

## Summary
The ETH withdrawal lifecycle in `LRTWithdrawalManager` is split across two separate transactions. In Phase 1 (`unlockQueue`), rsETH is irreversibly burned and ETH is deposited into Aave using a `try/catch` that silently tolerates Aave failures. In Phase 2 (`completeWithdrawal`), the withdrawal from Aave uses a hard external call with no failure tolerance. If Aave is paused between these two phases, users whose rsETH has already been burned cannot complete their ETH withdrawals, and all admin-level escape hatches (`emergencyWithdrawFromAave`, `setAaveIntegrationEnabled(false)`) also route through the same failing `_withdrawFromAave` call, leaving no recovery path until Aave resumes.

## Finding Description

**Phase 1 — `unlockQueue` (operator-only):**
At line 305, rsETH is burned from the contract. At line 307, ETH is redeemed from `LRTUnstakingVault`. At lines 310–316, the ETH is deposited into Aave via a `try/catch` self-call to `depositToAaveExternal`. Critically, if the Aave deposit fails, the catch block silently emits `AaveDepositFailed` and continues — ETH stays in the contract. If the deposit succeeds, ETH moves into Aave.

**Phase 2 — `completeWithdrawal` → `_processWithdrawalCompletion` (user-callable):**
At line 720–731, if `isAaveIntegrationEnabled && asset == ETH_TOKEN` and the contract's ETH balance is insufficient, `_withdrawFromAave(amountNeeded)` is called. Inside `_withdrawFromAave` at line 917:

```solidity
aaveWETHGateway.withdrawETH(aavePool, withdrawnAmount, address(this));
```

This is an unchecked external call. If Aave is paused, it reverts, causing the entire `completeWithdrawal` transaction to revert. The Solidity revert restores the in-transaction state changes (the `delete withdrawalRequests[requestId]` at line 712 and the `popFront()` at line 705 are undone), so the withdrawal request is preserved. However, the rsETH burned in Phase 1 is in a prior committed transaction and is not restored.

**Admin escape hatches also fail under Aave pause:**
- `emergencyWithdrawFromAave` (line 560) calls `_withdrawFromAave(amount)` — same failing path.
- `setAaveIntegrationEnabled(false)` (lines 486–501) calls `_withdrawFromAave(aaveBalance)` — same failing path.

Both admin functions revert if Aave is paused, leaving no on-chain recovery path until Aave resumes.

**Root cause:** The asymmetry between Phase 1 (Aave deposit uses `try/catch`, tolerates failure) and Phase 2 (Aave withdrawal uses a bare external call, does not tolerate failure) means the contract can enter a state where ETH is locked in Aave and no path exists to retrieve it while Aave is unavailable.

## Impact Explanation

**Temporary freezing of funds (Medium).** Any user who has an unlocked ETH withdrawal (rsETH already burned in Phase 1) cannot retrieve their ETH for the duration of Aave's unavailability. The withdrawal request remains in the queue, so funds are not permanently lost — but the user has no independent path to recover ETH. Recovery requires Aave to resume normal operation, after which either the user retries `completeWithdrawal` or an admin calls `emergencyWithdrawFromAave`. If Aave's pause is extended, the freeze persists indefinitely with no user-accessible bypass.

## Likelihood Explanation

Aave v3 has a guardian role that can pause the protocol in response to detected anomalies or exploits; this has occurred on Ethereum mainnet. The vulnerable window requires: (a) `isAaveIntegrationEnabled = true`, (b) `unlockQueue` has been called and ETH successfully deposited to Aave, and (c) Aave becomes unavailable before users call `completeWithdrawal`. This is an operationally realistic scenario. No attacker action is required — the freeze is triggered by a normal Aave guardian pause combined with the contract's own design.

## Recommendation

Apply the same `try/catch` tolerance to the withdrawal path in `_processWithdrawalCompletion`. If `_withdrawFromAave` fails, fall back to serving the withdrawal from any idle ETH balance in the contract. If neither source is sufficient, revert with a specific error but do not permanently block the user. Additionally, fix `setAaveIntegrationEnabled(false)` and `emergencyWithdrawFromAave` to handle the case where Aave itself is paused — for example, by allowing the admin to force-disable Aave integration without withdrawing (accepting that ETH remains in Aave to be claimed later), so that subsequent user withdrawals can be served from idle contract ETH once it is replenished.

## Proof of Concept

1. Deploy with `isAaveIntegrationEnabled = true` and Aave addresses configured.
2. User calls `initiateWithdrawal(ETH_TOKEN, rsETHAmount)` — rsETH transferred to contract.
3. Operator calls `unlockQueue(ETH_TOKEN, ...)`:
   - Line 305: rsETH burned from contract (committed, irreversible).
   - Line 307: ETH redeemed from `LRTUnstakingVault`.
   - Lines 310–316: `depositToAaveExternal` succeeds; ETH moves to Aave. `totalETHDepositedToAave` is incremented.
4. Simulate Aave guardian pause (fork test: call `POOL_ADDRESSES_PROVIDER.setPoolImpl` or use a mock that reverts on `withdrawETH`).
5. User calls `completeWithdrawal(ETH_TOKEN)`:
   - Line 705: nonce popped (will be restored on revert).
   - Line 712: `withdrawalRequests[requestId]` deleted (will be restored on revert).
   - Line 722: `address(this).balance < request.expectedAssetAmount` (ETH is in Aave).
   - Line 724: `_withdrawFromAave(amountNeeded)` called.
   - Line 917: `aaveWETHGateway.withdrawETH(...)` reverts — Aave is paused.
   - Entire transaction reverts. Withdrawal request is restored in queue.
6. User's rsETH is gone (burned in step 3). User cannot retrieve ETH. Retry of step 5 continues to revert.
7. Confirm `emergencyWithdrawFromAave` also reverts (line 560 → line 917 same path).
8. Confirm `setAaveIntegrationEnabled(false)` also reverts (line 495 → line 917 same path).
9. ETH is inaccessible until Aave unpauses.