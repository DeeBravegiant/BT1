Audit Report

## Title
ETH Transfer Revert in `_processWithdrawalCompletion` Permanently Freezes Funds and Blocks `sweepRemainingAssets` - (File: contracts/LRTWithdrawalManager.sol)

## Summary

When `_transferAsset` reverts due to an ETH recipient contract rejecting the transfer, Solidity rolls back all prior state mutations in `_processWithdrawalCompletion` — including the `unlockedWithdrawalsCount[asset]--` decrement. Because no admin cancellation path exists, the withdrawal is permanently uncompletable, the counter stays permanently above zero, and `sweepRemainingAssets(ETH)` is permanently blocked with `PendingWithdrawalsExist`. The user's ETH (backed by already-burned rsETH) is frozen in the contract with no recovery mechanism.

## Finding Description

Inside `_processWithdrawalCompletion`, state mutations occur in this order before the external call:

1. **L705** — `userAssociatedNonces[asset][user].popFront()` removes the nonce from the queue.
2. **L712** — `delete withdrawalRequests[requestId]` clears the request.
3. **L717** — `unlockedWithdrawalsCount[asset]--` decrements the counter.
4. **L734** — `_transferAsset(asset, user, request.expectedAssetAmount)` performs the ETH push.

`_transferAsset` uses a low-level call and reverts with `EthTransferFailed` if `sent == false`:

```solidity
(bool sent,) = payable(to).call{ value: amount }("");
if (!sent) revert EthTransferFailed();
```

Because the revert at L879 unwinds the entire transaction, all three prior mutations (L705, L712, L717) are rolled back. The withdrawal request is restored to the queue and `unlockedWithdrawalsCount[ETH]` returns to its pre-call value (still > 0).

Neither `completeWithdrawal` nor `completeWithdrawalForUser` can bypass this — both route through `_processWithdrawalCompletion` with the same `user` address. No function in the contract provides an admin path to cancel, redirect, or force-complete the stuck request. A grep for `cancel`, `rescue`, `recover`, `forceComplete`, and `adminWithdraw` returns no matches.

`sweepRemainingAssets` gates unconditionally on `hasUnlockedWithdrawals`:

```solidity
if (hasUnlockedWithdrawals(asset)) revert PendingWithdrawalsExist();
```

`hasUnlockedWithdrawals` returns `unlockedWithdrawalsCount[asset] > 0`. With the counter permanently stuck above zero, `sweepRemainingAssets(ETH)` is permanently blocked.

## Impact Explanation

**Medium — Permanent freezing of unclaimed yield, with a secondary permanent freezing of the user's own ETH.**

- The user's rsETH is burned at `initiateWithdrawal` time. The ETH equivalent (`expectedAssetAmount`) is committed in `assetsCommitted` and held in the contract. With no cancellation path, this ETH is permanently frozen.
- Any residual ETH that accumulates in the withdrawal manager (rounding dust, direct sends, or Aave withdrawal remainders) cannot be swept to the treasury because `sweepRemainingAssets(ETH)` is permanently blocked. The protocol's own comment acknowledges ETH sweeping is "not expected to be used for ETH," but the function exists and is the only recovery path for raw ETH balance accumulation.
- The `collectInterestToTreasury` path handles Aave aWETH interest separately and is unaffected, but raw ETH balance has no other recovery mechanism once the sweep is blocked.

## Likelihood Explanation

Low-to-Medium. The trigger condition — a smart contract address initiating an ETH withdrawal without a working `receive()` — arises both accidentally (e.g., a Gnosis Safe that has not enabled ETH receipt, a proxy contract, a multisig) and deliberately (a griefing contract that reverts on receive). `initiateWithdrawal` is permissionless; no privileged role is required. A single such withdrawal, once unlocked by an operator, permanently blocks the sweep function for ETH. The attacker sacrifices their own rsETH/ETH to achieve this.

## Recommendation

1. **Add an admin escape hatch**: Introduce a manager-only function that cancels a stuck withdrawal request — returning or burning the committed rsETH, decrementing `unlockedWithdrawalsCount`, and clearing `assetsCommitted` — without attempting the ETH transfer.
2. **Allow withdrawal redirection**: Let the user or an operator specify an alternative recipient address so a stuck contract address can be bypassed.
3. **Decouple the counter decrement from the transfer**: Move `unlockedWithdrawalsCount[asset]--` to after a confirmed successful transfer, or adopt a pull-payment pattern where the request is marked claimable and the user pulls funds rather than having them pushed.

## Proof of Concept

```solidity
// 1. Deploy a contract with no receive() (any ETH transfer to it reverts)
contract RevertOnReceive {
    function attack(address wm, address rsETH, uint256 amt) external {
        IERC20(rsETH).approve(wm, amt);
        ILRTWithdrawalManager(wm).initiateWithdrawal(ETH_TOKEN, amt, "");
    }
}

// 2. Fund RevertOnReceive with rsETH, call attack()
// 3. Operator calls unlockQueue(ETH, ...) → unlockedWithdrawalsCount[ETH] = 1
// 4. Anyone calls completeWithdrawal(ETH, ...) targeting RevertOnReceive
//    → _transferAsset reverts with EthTransferFailed
//    → entire tx reverts; unlockedWithdrawalsCount[ETH] stays 1
// 5. Assert: hasUnlockedWithdrawals(ETH) == true  (permanently)
// 6. Manager calls sweepRemainingAssets(ETH)
//    → reverts with PendingWithdrawalsExist  (permanently)
// 7. ETH balance in withdrawal manager grows with no recovery path
```

Foundry fork test: deploy `RevertOnReceive` on a mainnet fork, execute steps 2–6, assert `sweepRemainingAssets` always reverts and `address(withdrawalManager).balance` is unrecoverable.