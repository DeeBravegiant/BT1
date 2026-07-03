Audit Report

## Title
Dust Withdrawal Requests Enable Queue Griefing with Temporary Freezing of Legitimate Withdrawals - (File: contracts/LRTWithdrawalManager.sol)

## Summary

`minRsEthAmountToWithdraw` is never initialised in `initialize`, defaulting to `0` for every asset. This allows any user to queue withdrawal requests of 1 wei of rsETH. Because `_unlockWithdrawalRequests` processes the FIFO queue in strict order and dust entries produce a `payoutAmount` of 0 or 1 wei (never triggering the `availableAssetAmount < payoutAmount` break for any non-empty vault), an attacker who front-fills the queue forces the operator to iterate over every dust entry before reaching legitimate requests, temporarily freezing legitimate users' withdrawals and imposing unbounded cumulative gas costs on the operator.

## Finding Description

**Root cause — uninitialised minimum:**

`minRsEthAmountToWithdraw` is declared at line 35 but never set in `initialize` (lines 90–98). The only guard in `initiateWithdrawal` is:

```solidity
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}
```

When the mapping value is `0`, this reduces to `rsETHUnstaked == 0`, so any amount ≥ 1 wei passes.

**Dust payout behaviour:**

`_calculatePayoutAmount` computes:

```solidity
uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```

For `rsETHUnstaked = 1`:
- When `rsETHPrice ≥ assetPrice` (e.g. ETH, stETH): `currentReturn = 1`, so `payoutAmount = 1`.
- When `rsETHPrice < assetPrice` (e.g. a highly-appreciated LST): `currentReturn = 0`, so `payoutAmount = 0`.

In both cases the break condition `availableAssetAmount < payoutAmount` is either never triggered (payoutAmount = 0) or only triggered when the vault is fully drained (payoutAmount = 1). The loop therefore iterates over every dust entry up to `firstExcludedIndex` before it can reach legitimate requests.

**FIFO ordering enforces sequential processing:**

`_unlockWithdrawalRequests` iterates from `nextLockedNonce[asset]` upward. Legitimate requests queued after the dust entries cannot be unlocked until all preceding nonces are processed. Each iteration performs multiple SLOADs and SSTOREs (`withdrawalRequests`, `assetsCommitted`, `unlockedWithdrawalsCount`), making large batches of dust entries expensive.

**`firstExcludedIndex` does not eliminate the problem:**

While the operator can cap iterations per call, they must still drain all dust entries in sequence before any legitimate withdrawal can be unlocked. Each batch call costs gas with zero economic output, and legitimate users remain locked out for the entire duration.

## Impact Explanation

Legitimate users cannot call `completeWithdrawal` until their nonce is below `nextLockedNonce[asset]`, which only advances after all preceding dust entries are processed. This constitutes **temporary freezing of funds** (Medium) for legitimate withdrawers. Additionally, the operator faces **unbounded cumulative gas consumption** (Medium) proportional to the number of dust entries, with no economic return per iteration.

## Likelihood Explanation

The attack requires only rsETH tokens (obtainable by any depositor via `LRTDepositPool`) and repeated calls to the public `initiateWithdrawal`. No privileged access is needed. The cost per dust entry is ~50k gas plus 1 wei of rsETH (locked but eventually returned as 0–1 wei of asset). Queuing tens of thousands of entries across multiple transactions is economically feasible relative to the disruption caused. The default `minRsEthAmountToWithdraw = 0` is present on every new deployment unless the admin explicitly calls `setMinRsEthAmountToWithdraw`.

## Recommendation

1. **Set a non-zero minimum in `initialize`.** Initialise `minRsEthAmountToWithdraw` for every supported asset to a sensible floor (e.g. `0.001 ether`) rather than relying on an optional admin call.

2. **Reject zero-payout requests at queue time.** In `initiateWithdrawal`, revert if `expectedAssetAmount == 0`:
   ```solidity
   uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
   if (expectedAssetAmount == 0) revert InvalidAmountToWithdraw();
   ```

3. **Add a per-call iteration cap** in `_unlockWithdrawalRequests` as defence-in-depth to prevent any single `unlockQueue` call from consuming the full block gas budget regardless of queue contents.

## Proof of Concept

1. Admin deploys `LRTWithdrawalManager` and never calls `setMinRsEthAmountToWithdraw` for ETH (default = 0).
2. Attacker acquires rsETH by depositing ETH into `LRTDepositPool`.
3. Attacker calls `initiateWithdrawal(ETH_TOKEN, 1, "")` repeatedly across multiple transactions, queuing N dust entries (e.g. N = 50 000). Each call commits 1 wei to `assetsCommitted[ETH_TOKEN]`.
4. Legitimate users queue real withdrawal requests; their nonces are appended after the N dust entries.
5. Operator calls `unlockQueue(ETH_TOKEN, N + 1, ...)`. The loop iterates N times, each performing SLOAD/SSTORE on `withdrawalRequests`, `assetsCommitted`, and `unlockedWithdrawalsCount`. At N = 50 000 the call reverts out-of-gas.
6. Operator reduces `firstExcludedIndex` to 1 000 and calls repeatedly; each call processes only dust entries at ~3–5M gas with zero economic output.
7. Legitimate users' withdrawal requests remain locked until all N dust entries are drained, temporarily freezing their funds.