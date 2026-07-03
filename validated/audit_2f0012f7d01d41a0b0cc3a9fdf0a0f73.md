Audit Report

## Title
Aave Reserve Pause Permanently Blocks ETH Withdrawals With No Admin Escape Hatch — (`contracts/LRTWithdrawalManager.sol`)

## Summary

`LRTWithdrawalManager` deposits unlocked ETH into Aave v3 to earn yield. When a user calls `completeWithdrawal`, the contract calls `_withdrawFromAave` unconditionally if its ETH balance is insufficient. If Aave's WETH reserve is paused, this call reverts with no fallback. Every admin escape hatch — `setAaveIntegrationEnabled(false)` and `emergencyWithdrawFromAave` — also calls `aaveWETHGateway.withdrawETH` internally and reverts identically, leaving the protocol with no code path to service user withdrawals or disable the integration while Aave is unavailable.

## Finding Description

**Root cause 1 — `_processWithdrawalCompletion` has no fallback when Aave reverts:**

At `LRTWithdrawalManager.sol` L720–724, when `isAaveIntegrationEnabled` is `true` and `address(this).balance < request.expectedAssetAmount`, `_withdrawFromAave(amountNeeded)` is called unconditionally with no `try/catch`. At L917, `_withdrawFromAave` calls `aaveWETHGateway.withdrawETH(aavePool, withdrawnAmount, address(this))` directly. If Aave has paused the WETH reserve, this call reverts, propagating the revert up through `completeWithdrawal` and `completeWithdrawalForUser`. The user's rsETH was already burned at `initiateWithdrawal` time; their withdrawal request is stuck with no recourse.

**Root cause 2 — `setAaveIntegrationEnabled(false)` cannot flip the flag while Aave is paused:**

At L486–503, when `enabled = false`, the function first calls `_collectInterestToTreasury()` (L490), which at L954 calls `aaveWETHGateway.withdrawETH` directly with no try/catch. If Aave is paused, this reverts before reaching `isAaveIntegrationEnabled = enabled` at L503. The flag is never set to `false`, so the broken withdrawal path remains active.

**Root cause 3 — `emergencyWithdrawFromAave` also calls `_withdrawFromAave`:**

At L558–560, the PAUSER_ROLE emergency function calls `_collectInterestToTreasury()` then `_withdrawFromAave(amount)`. Both paths call `aaveWETHGateway.withdrawETH` with no try/catch. There is no code path that bypasses the gateway.

**Asymmetry with deposit path:**

At L311–316, deposits use `try this.depositToAaveExternal(assetAmountUnlocked) { } catch { ... }` — silently failing and leaving ETH in the contract. Withdrawals have no equivalent resilience. ETH flows into Aave even when Aave is degraded but cannot flow back out, creating a one-way ratchet.

## Impact Explanation

When Aave's WETH reserve is paused:
1. All calls to `completeWithdrawal` and `completeWithdrawalForUser` revert — users whose rsETH has already been burned cannot recover their ETH.
2. `setAaveIntegrationEnabled(false)` reverts before flipping the flag.
3. `emergencyWithdrawFromAave` reverts.
4. All ETH deposited to Aave is inaccessible with no protocol-level escape hatch.

This constitutes **Temporary freezing of funds** (Medium) for the duration of the Aave pause. If the reserve is deprecated or the pause is indefinite, it escalates to **Permanent freezing of funds** (Critical), since rsETH has already been burned and the withdrawal request cannot be re-initiated.

## Likelihood Explanation

No attacker action is required. Aave governance has paused individual reserves during past security incidents (e.g., the Aave v2 CRV bad debt incident, Aave v3 emergency mode activations). Pausing a reserve is a standard, documented Aave governance operation. The `LRTWithdrawalManager` holds aWETH on behalf of rsETH withdrawers; any WETH reserve pause directly triggers this freeze for all users with pending unlocked withdrawals whose ETH was deposited to Aave via `unlockQueue`.

## Recommendation

1. **Add `try/catch` in `_processWithdrawalCompletion`** around the `_withdrawFromAave` call. On failure, revert with a descriptive error without corrupting state, and add a separate admin function to service withdrawals directly from contract ETH balance when Aave is unavailable.

2. **Separate the "disable" action from the "withdraw" action** in `setAaveIntegrationEnabled`. Allow `isAaveIntegrationEnabled = false` to be set independently of any Aave interaction, and provide a separate `withdrawAllFromAave` function callable when Aave is available.

3. **Add a force-disable function** callable by PAUSER_ROLE that sets `isAaveIntegrationEnabled = false` without any Aave interaction, so `completeWithdrawal` immediately falls back to contract ETH balance.

## Proof of Concept

1. Manager enables Aave integration and calls `unlockQueue` for ETH. Unlocked ETH is deposited to Aave via the try/catch path at L311. `totalETHDepositedToAave > 0`, `address(this).balance ≈ 0`.
2. Aave governance pauses the WETH reserve (documented governance action).
3. User whose withdrawal was unlocked in step 1 calls `completeWithdrawal(ETH_TOKEN, requestId, referralId)`.
4. `_processWithdrawalCompletion` at L721–724: `address(this).balance < request.expectedAssetAmount` → true → calls `_withdrawFromAave(amountNeeded)`.
5. `_withdrawFromAave` at L917: `aaveWETHGateway.withdrawETH(aavePool, withdrawnAmount, address(this))` → reverts (reserve paused).
6. Entire `completeWithdrawal` reverts. User's rsETH is already burned; withdrawal request is stuck.
7. Manager calls `setAaveIntegrationEnabled(false)` → reverts at L490 (`_collectInterestToTreasury` → `aaveWETHGateway.withdrawETH` at L954 → reverts). Flag never flipped.
8. PAUSER_ROLE calls `emergencyWithdrawFromAave(amount)` → reverts at L558 (`_collectInterestToTreasury` → same revert).
9. All user ETH withdrawals are frozen for the duration of the Aave pause with no protocol escape hatch.

**Foundry fork test plan:** Fork mainnet with Aave v3 deployed. Deploy `LRTWithdrawalManager`, enable Aave integration, call `unlockQueue` to deposit ETH to Aave. Use `vm.prank(aaveGovernance)` to call `setReservePause(weth, true)` on the Aave pool configurator. Then call `completeWithdrawal` and assert it reverts. Call `setAaveIntegrationEnabled(false)` and assert it reverts. Call `emergencyWithdrawFromAave` and assert it reverts. Confirm `isAaveIntegrationEnabled` remains `true` and user funds are inaccessible.