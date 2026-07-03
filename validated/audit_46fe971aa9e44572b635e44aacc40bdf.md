Audit Report

## Title
Unbounded Withdrawal Queue Spam via Zero `minRsEthAmountToWithdraw` Enables Temporary Freezing of Legitimate Withdrawals — (`File: contracts/LRTWithdrawalManager.sol`)

## Summary

`minRsEthAmountToWithdraw[asset]` defaults to `0` for any asset not explicitly configured by the admin. When this value is `0`, the only effective guard in `initiateWithdrawal` is `rsETHUnstaked == 0`, meaning any amount ≥ 1 wei passes. An attacker holding rsETH can flood the FIFO withdrawal queue with arbitrarily many dust entries, forcing the operator to drain all spam entries before legitimate users' requests can be unlocked, causing a temporary (and at scale, effectively indefinite) freeze of legitimate withdrawals.

## Finding Description

`initiateWithdrawal` enforces:

```solidity
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}
```

Because `minRsEthAmountToWithdraw` is a `mapping(address => uint256)` that defaults to `0`, and `initialize` never sets a non-zero value for any asset, the condition `rsETHUnstaked < 0` is vacuously false for all `uint256` values. Only the `rsETHUnstaked == 0` guard is active. Any amount ≥ 1 wei is accepted.

Each call to `initiateWithdrawal` with 1 wei of rsETH:
1. Transfers 1 wei rsETH from the attacker to the contract.
2. Computes `expectedAssetAmount = 1 * rsETHPrice / assetPrice` (≈ 1 wei for ETH).
3. Checks `expectedAssetAmount > getAvailableAssetAmount(asset)` — passes as long as committed assets don't exceed total assets.
4. Increments `assetsCommitted[asset]` by ~1 wei and pushes a new nonce into the global FIFO queue.

The queue is strictly FIFO: `_unlockWithdrawalRequests` advances `nextLockedNonce` sequentially. Legitimate users who queue after the spam cannot be unlocked until all preceding spam entries are processed. While the operator controls `firstExcludedIndex` to batch-process entries, with an attacker holding `N` wei of rsETH, `N` spam entries are created. Processing 10,000 entries per call against 10^18 entries (1 ETH of rsETH) requires 10^14 operator calls — effectively permanent for any meaningful rsETH balance.

The `ExceedAmountToWithdraw` check at L170 bounds total committed assets to available TVL, but at 1 wei per entry, the entry count equals the available TVL in wei (e.g., 1,000 ETH = 10^21 entries maximum).

## Impact Explanation

**Medium — Temporary freezing of funds.**

Legitimate users' withdrawal requests, queued after the spam, cannot be unlocked until all spam entries ahead of them are processed. The operator must make an unbounded number of `unlockQueue` calls to drain the spam queue. At sufficient scale (attacker with meaningful rsETH holdings), this delay is effectively indefinite, freezing legitimate users' funds in the contract.

## Likelihood Explanation

Any rsETH holder can trigger this without any privileged access. The attacker's rsETH is returned when their spam entries are eventually processed — the only cost is gas per `initiateWithdrawal` call. `minRsEthAmountToWithdraw` is not set in `initialize`, so every fresh deployment and every newly supported asset is immediately vulnerable. The attack is repeatable: after spam entries are processed and rsETH is returned, the attacker can re-queue.

## Recommendation

1. **Set a non-zero default in `initialize`**: Assign a meaningful `minRsEthAmountToWithdraw` (e.g., 0.001 ETH worth of rsETH) for all supported assets at initialization time.
2. **Guard `initiateWithdrawal` against unconfigured minimums**: Revert if `minRsEthAmountToWithdraw[asset] == 0`, requiring the admin to explicitly configure a minimum before withdrawals are accepted for any asset.
3. **Per-user queue depth cap**: Reject `initiateWithdrawal` if the caller already has more than `N` pending requests, limiting per-user spam capacity.

## Proof of Concept

1. Deploy `LRTWithdrawalManager`; `minRsEthAmountToWithdraw[ETH_TOKEN]` is `0` (default, never set in `initialize`).
2. Attacker holds `X` wei of rsETH (e.g., `X = 10^18` for 1 ETH worth).
3. Attacker calls `initiateWithdrawal(ETH_TOKEN, 1, "")` in a loop across multiple blocks. Each call passes the check at L162 (`1 == 0` → false; `1 < 0` → false) and commits ~1 wei of assets, pushing a nonce into the queue.
4. A legitimate user calls `initiateWithdrawal` after the attacker, queuing behind all spam entries.
5. Operator calls `unlockQueue(ETH_TOKEN, nextUnusedNonce, ...)`. `_unlockWithdrawalRequests` must advance `nextLockedNonce` through all `X` spam entries before reaching the legitimate user's entry. Even with batched `firstExcludedIndex`, the operator requires `X / batchSize` separate transactions.
6. The legitimate user's withdrawal is frozen for the duration of spam processing. With `X = 10^18`, at 10,000 entries per batch, this requires `10^14` operator calls — effectively permanent.