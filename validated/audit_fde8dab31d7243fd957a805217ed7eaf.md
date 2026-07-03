Audit Report

## Title
Reverting ETH Recipient Permanently Freezes Unlocked ETH With No Admin Recovery Path - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`LRTWithdrawalManager._transferAsset` forwards ETH via an uncapped `.call{value: amount}("")` that hard-reverts on failure. Because rsETH is burned in a prior, already-finalized `unlockQueue` transaction, a contract recipient whose `receive()` reverts causes the corresponding ETH to be permanently locked inside `LRTWithdrawalManager` with no admin escape hatch.

## Finding Description
The withdrawal lifecycle is split across two independent transactions:

**Step 1 – `unlockQueue` (operator-only, L305-307):** rsETH is burned from the contract and ETH is redeemed from `LRTUnstakingVault` into `LRTWithdrawalManager`. This transaction is final and irreversible.

**Step 2 – `_processWithdrawalCompletion` (L699-738):** Called by `completeWithdrawal` or `completeWithdrawalForUser`. At L717, `unlockedWithdrawalsCount[asset]--` is decremented. At L734, `_transferAsset` is called.

`_transferAsset` (L876-883) sends ETH via:
```solidity
(bool sent,) = payable(to).call{ value: amount }("");
if (!sent) revert EthTransferFailed();
```

If the recipient is a contract whose `receive()` reverts, `_transferAsset` reverts with `EthTransferFailed`, rolling back the entire Step 2 transaction — including the `unlockedWithdrawalsCount` decrement at L717. The rsETH burn from Step 1 is **not** rolled back.

The only potential recovery path, `sweepRemainingAssets` (L395-413), is gated on:
```solidity
if (hasUnlockedWithdrawals(asset)) revert PendingWithdrawalsExist();
```
Since `unlockedWithdrawalsCount[asset]` remains > 0 (the decrement was reverted), `sweepRemainingAssets` is permanently blocked. No other admin recovery function exists in the contract. The ETH is permanently locked, and the corresponding rsETH is permanently burned.

The comment at L191 acknowledges gas-grief awareness for `completeWithdrawalForUser` but incorrectly dismisses it as "non-impactful for ETH." There is no code restriction preventing ETH withdrawals from being processed through `completeWithdrawalForUser`, and the permanent-freeze scenario is categorically more severe than gas grief.

## Impact Explanation
After `unlockQueue` finalizes, the rsETH burn is irreversible and the ETH is held in `LRTWithdrawalManager`. If the beneficiary address cannot accept ETH, that ETH is permanently locked with no admin escape hatch. This constitutes **Critical — Permanent freezing of funds**.

## Likelihood Explanation
Smart-contract wallets, multisigs, and protocol-owned accounts that do not implement a `receive()` function are common depositors in LRT protocols. Any such account that initiates an ETH withdrawal will trigger this freeze once the operator runs `unlockQueue`. No special attacker capability is required — a user simply needs to initiate a withdrawal from a contract address without a payable fallback. The scenario is also reachable by a deliberate attacker deploying a contract with a reverting `receive()`.

## Recommendation
1. **Pull-payment pattern (preferred):** Record owed ETH per user in a mapping and let them claim it separately via a dedicated `claimETH()` function. This decouples delivery failure from accounting state.
2. **Cap gas on ETH transfer:** Use `call{value: amount, gas: 2300}` and on failure credit the amount to a per-user claimable balance rather than reverting.
3. **Admin override for stuck withdrawals:** Add a manager-only function that can redirect a stuck withdrawal to an alternate address when the primary recipient is provably unable to receive ETH.

## Proof of Concept
1. Attacker deploys `MaliciousWallet`:
   ```solidity
   receive() external payable { revert(); }
   ```
2. `MaliciousWallet` calls `initiateWithdrawal(ETH_TOKEN, rsETHAmount, "")`. rsETH is transferred to `LRTWithdrawalManager`.
3. Operator calls `unlockQueue(ETH_TOKEN, ...)`. rsETH is burned (L305); ETH is moved from `LRTUnstakingVault` into `LRTWithdrawalManager` (L307). Transaction succeeds and is **final**.
4. `MaliciousWallet` calls `completeWithdrawal(ETH_TOKEN, "")`. Execution reaches `_transferAsset` → `payable(MaliciousWallet).call{value: amount}("")` → `receive()` reverts → entire tx reverts. `unlockedWithdrawalsCount` decrement is rolled back.
5. Operator calls `completeWithdrawalForUser(ETH_TOKEN, MaliciousWallet, "")`. Same revert.
6. Manager calls `sweepRemainingAssets(ETH_TOKEN)` → reverts with `PendingWithdrawalsExist` because `unlockedWithdrawalsCount[ETH_TOKEN] > 0`.
7. ETH is permanently locked in `LRTWithdrawalManager`. rsETH is permanently burned. No recovery path exists.