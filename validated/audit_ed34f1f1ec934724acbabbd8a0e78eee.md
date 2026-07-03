Audit Report

## Title
Missing ETH Receivability Check in `initiateWithdrawal` Enables Permanent Fund Freeze - (File: contracts/LRTWithdrawalManager.sol)

## Summary

`LRTWithdrawalManager::initiateWithdrawal` accepts rsETH from a contract caller for ETH-asset withdrawals without verifying the caller can receive native ETH. After the operator's `unlockQueue` irreversibly burns the rsETH and moves ETH into the manager, any subsequent `completeWithdrawal` call for a beneficiary contract lacking a `payable receive()`/`fallback()` will always revert. The rsETH is permanently destroyed and the corresponding ETH is permanently locked in `LRTWithdrawalManager` with no admin escape hatch.

## Finding Description

The withdrawal lifecycle proceeds across three transactions:

**Step 1 — `initiateWithdrawal` (L150–178):** `msg.sender` is recorded as the beneficiary via `userAssociatedNonces[asset][msg.sender].pushBack(...)` at L756. rsETH is pulled from `msg.sender` at L166. No check verifies that `msg.sender` can receive ETH.

**Step 2 — `unlockQueue` (L268–320):** The operator calls this function. At L305, `IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned)` permanently destroys the rsETH. At L307, `unstakingVault.redeem(asset, assetAmountUnlocked)` moves the corresponding ETH into `LRTWithdrawalManager`. Both operations are in a separate transaction from Step 3 and cannot be rolled back.

**Step 3 — `completeWithdrawal` / `completeWithdrawalForUser` (L183–204):** Both functions call `_processWithdrawalCompletion(asset, user, ...)`, which at L734 calls `_transferAsset(asset, user, request.expectedAssetAmount)`. Inside `_transferAsset` (L876–883), for ETH: `(bool sent,) = payable(to).call{ value: amount }("")`. If `to` is a contract without `receive()`/`fallback()`, this returns `false` and reverts with `EthTransferFailed`. The entire transaction reverts, restoring the withdrawal request record — but the rsETH burned in Step 2 is not restored.

**No escape hatch exists:**
- `completeWithdrawalForUser` (operator-only, L192–204) still sends ETH to the same `user` address — it does not allow redirecting to a different recipient.
- `sweepRemainingAssets` (L395–414) is gated on `hasUnlockedWithdrawals(asset) == false` (L403), but the stuck withdrawal keeps `unlockedWithdrawalsCount[asset] > 0` (decremented at L717, which is reverted on each failed attempt), permanently blocking the sweep.
- There is no function to change the beneficiary of an existing withdrawal request.

Recovery requires a contract upgrade.

## Impact Explanation

**Critical — Permanent freezing of funds.** Once `unlockQueue` executes, the rsETH is gone and the ETH is in the manager. If the beneficiary contract cannot receive ETH, both assets are unrecoverable without a protocol upgrade. This matches the Critical "Permanent freezing of funds" impact class exactly.

## Likelihood Explanation

**Low-Medium.** The affected caller must be a smart contract that: (1) holds rsETH, (2) calls `initiateWithdrawal` for the ETH asset, and (3) lacks a `payable receive()` or `fallback()` function. This is a realistic scenario for protocol integrators, yield aggregators, or vaults that interact programmatically with the withdrawal manager without anticipating ETH receipt. The condition is not exotic and mirrors the class of contracts that caused the Ignite bug referenced in the report. The scenario is self-inflicted by the integrating contract but the protocol provides no safeguard.

## Recommendation

Add a zero-value ETH receivability check at the top of `initiateWithdrawal`, before any state changes or token transfers, when `asset == LRTConstants.ETH_TOKEN`:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    (bool canReceive,) = msg.sender.call("");
    if (!canReceive) revert RecipientCannotReceiveETH();
}
```

This must be placed before the `safeTransferFrom` at L166 so no state change occurs if the check fails. The existing `nonReentrant` modifier on `initiateWithdrawal` covers this external call path.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.15;
import "forge-std/Test.sol";

contract NoReceiveContract {
    // No receive() or fallback()
}

contract TestFreezePoC is Test {
    function test_rootCondition() external {
        address noReceive = address(new NoReceiveContract());
        (bool success,) = noReceive.call{value: 0}("");
        assertEq(success, false); // zero-value call fails — ETH transfer will always revert
    }
}
```

Full fork test sequence:
1. Deploy `NoReceiveContract`; fund it with rsETH via a transfer.
2. From `NoReceiveContract`, call `initiateWithdrawal(ETH_TOKEN, amount, "")` — succeeds; rsETH locked in manager.
3. Operator calls `unlockQueue(ETH_TOKEN, ...)` — rsETH burned at L305, ETH moved to manager at L307.
4. Call `completeWithdrawal(ETH_TOKEN, "")` from `NoReceiveContract` — reverts at `_transferAsset` L878–879 (`EthTransferFailed`).
5. Call `completeWithdrawalForUser(ETH_TOKEN, address(noReceive), "")` as operator — same revert.
6. Call `sweepRemainingAssets(ETH_TOKEN)` as manager — reverts at L403 (`PendingWithdrawalsExist`).
7. Assert: rsETH supply decreased, ETH balance of manager > 0, withdrawal request still present — funds permanently frozen.