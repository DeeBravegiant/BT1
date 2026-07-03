Audit Report

## Title
ETH Withdrawal Permanently Frozen for Contract Recipients That Revert on ETH Receive - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`LRTWithdrawalManager` uses a push-payment model to deliver ETH to withdrawing users. If the recipient is a contract that cannot receive ETH, `completeWithdrawal` permanently reverts for that user. Because rsETH is burned in a prior, separate transaction (`unlockQueue`), the user's rsETH is irreversibly destroyed while the corresponding ETH is frozen inside `LRTWithdrawalManager` with no on-chain recovery path.

## Finding Description
The internal helper `_transferAsset` delivers ETH via a low-level call:

```solidity
// contracts/LRTWithdrawalManager.sol L876-879
function _transferAsset(address asset, address to, uint256 amount) internal {
    if (asset == LRTConstants.ETH_TOKEN) {
        (bool sent,) = payable(to).call{ value: amount }("");
        if (!sent) revert EthTransferFailed();
    }
```

This is the terminal step of `_processWithdrawalCompletion` (L734), called by both `completeWithdrawal` (L183) and `completeWithdrawalForUser` (L192).

The critical ordering spans **two separate transactions**:

**Transaction 1 – `unlockQueue`** (L301–307): rsETH is **permanently burned** via `burnFrom` and ETH is redeemed from `LRTUnstakingVault` into `LRTWithdrawalManager`. This transaction is finalized on-chain.

**Transaction 2 – `completeWithdrawal`** (L183 → L734): `_transferAsset` pushes ETH to `user`. If `user` is a contract with no `receive()` or a reverting fallback, `payable(to).call{value: amount}("")` returns `success = false`, triggering `revert EthTransferFailed()`. The entire transaction reverts, restoring in-transaction state (nonce pop, request deletion, `unlockedWithdrawalsCount--`). However, the rsETH burn from Transaction 1 is **not undone**.

Recovery via `sweepRemainingAssets` is also permanently blocked:
```solidity
// L403
if (hasUnlockedWithdrawals(asset)) revert PendingWithdrawalsExist();
```
`unlockedWithdrawalsCount[asset]` remains > 0 because the stuck withdrawal is still counted as unlocked. There is no admin function to forcibly redirect the ETH to an alternate address or cancel the stuck request.

The NatDoc comment on `completeWithdrawalForUser` (L191) explicitly states *"Not expected to be used for ETH; potential gas grief scenarios are non-impactful for ETH"*, confirming developer awareness of the fragility of ETH push-delivery to arbitrary addresses — yet no alternative delivery mechanism exists.

## Impact Explanation
**Critical — Permanent freezing of funds.** A user whose withdrawal address is a smart contract that cannot receive ETH will have their rsETH permanently burned (Transaction 1 is irreversible) while the corresponding ETH is frozen inside `LRTWithdrawalManager` indefinitely. No on-chain path exists to recover or redirect the frozen ETH: `completeWithdrawal` and `completeWithdrawalForUser` both revert, and `sweepRemainingAssets` is gated behind `hasUnlockedWithdrawals` which remains true. This is a direct, permanent loss of user funds.

## Likelihood Explanation
Smart-contract wallets (Gnosis Safe variants, protocol-owned treasuries, custom multisigs) routinely hold rsETH and initiate withdrawals. Many such contracts do not implement a `receive()` function or implement one that conditionally reverts. The scenario requires no attacker — it is triggered by the normal withdrawal flow of any such contract. The operator-assisted path (`completeWithdrawalForUser`) provides no relief since it calls the same `_processWithdrawalCompletion` function. Likelihood is medium-to-high given the prevalence of smart-contract wallets in DeFi.

## Recommendation
Replace the push-payment model for ETH withdrawals with a pull-payment (claimable) model:
- In `_processWithdrawalCompletion`, instead of calling `_transferAsset` immediately, record the owed amount in a `mapping(address => uint256) public claimableETH`.
- Add a separate `claimETH()` function that allows the user to pull their ETH at any time.
- This eliminates the dependency on the recipient's ability to receive ETH and decouples delivery failure from the withdrawal accounting state.

## Proof of Concept
1. A smart-contract wallet (no `receive()`) holding rsETH calls `initiateWithdrawal(ETH_TOKEN, rsETHAmount, "")`. rsETH is transferred to `LRTWithdrawalManager`.
2. Operator calls `unlockQueue(ETH_TOKEN, ...)`. At L305, `burnFrom` permanently burns the rsETH. At L307, ETH is redeemed from `LRTUnstakingVault` into `LRTWithdrawalManager`. Transaction finalized.
3. The smart-contract wallet calls `completeWithdrawal(ETH_TOKEN, "")`. Inside `_processWithdrawalCompletion` at L734, `_transferAsset` executes `payable(wallet).call{value: amount}("")`. The wallet has no `receive()`, so the call returns `success = false`. `revert EthTransferFailed()` is triggered; the entire transaction reverts.
4. In-transaction state is restored (nonce, request, count). rsETH burn from step 2 is not undone. ETH remains in `LRTWithdrawalManager`.
5. Every subsequent call to `completeWithdrawal` or `completeWithdrawalForUser` for this user repeats step 3 and reverts.
6. `sweepRemainingAssets` is permanently blocked: `unlockedWithdrawalsCount[ETH_TOKEN] > 0` causes `revert PendingWithdrawalsExist()` at L403.
7. The user's ETH is permanently frozen with no recovery path.

**Foundry test plan:**
```solidity
function test_frozenETHForNonReceivableContract() public fork {
    // Deploy a contract with no receive()
    NoReceiveContract wallet = new NoReceiveContract();
    // Fund wallet with rsETH, initiate withdrawal
    vm.prank(address(wallet));
    withdrawalManager.initiateWithdrawal(ETH_TOKEN, rsETHAmount, "");
    // Operator unlocks queue (burns rsETH)
    vm.prank(operator);
    withdrawalManager.unlockQueue(ETH_TOKEN, params);
    // Verify rsETH is burned
    assertEq(rsETH.balanceOf(address(withdrawalManager)), 0);
    // completeWithdrawal always reverts
    vm.prank(address(wallet));
    vm.expectRevert(LRTWithdrawalManager.EthTransferFailed.selector);
    withdrawalManager.completeWithdrawal(ETH_TOKEN, "");
    // ETH is stuck, sweep is blocked
    vm.prank(manager);
    vm.expectRevert(LRTWithdrawalManager.PendingWithdrawalsExist.selector);
    withdrawalManager.sweepRemainingAssets(ETH_TOKEN);
}
```