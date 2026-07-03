Audit Report

## Title
Missing ETH Receivability Validation in `initiateWithdrawal` Causes Permanent Fund Freeze - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`LRTWithdrawalManager::initiateWithdrawal` accepts rsETH from any `msg.sender` — including smart contracts — for ETH-asset withdrawals without verifying that `msg.sender` can receive native ETH. After the operator calls `unlockQueue`, which irreversibly burns the rsETH held in the contract, any subsequent `completeWithdrawal` call by a beneficiary contract lacking a `payable` `receive()` or `fallback()` function will always revert. The rsETH is permanently destroyed and the corresponding ETH is permanently locked inside `LRTWithdrawalManager` with no admin escape hatch.

## Finding Description
The withdrawal lifecycle involves three steps across separate transactions:

**Step 1 — `initiateWithdrawal` (L150–178):** The caller (potentially a smart contract) invokes this function. rsETH is pulled from `msg.sender` at L166 via `safeTransferFrom`. No check is performed to verify that `msg.sender` can receive ETH. The withdrawal request is recorded in `withdrawalRequests` and `userAssociatedNonces`.

**Step 2 — `unlockQueue` (L268–320):** The operator calls this function. At L305, rsETH held by the contract is **permanently burned**: `IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned)`. At L307, the corresponding ETH is pulled from `LRTUnstakingVault` into `LRTWithdrawalManager`: `unstakingVault.redeem(asset, assetAmountUnlocked)`. This burn is irreversible and occurs in a separate transaction from Step 3.

**Step 3 — `completeWithdrawal` → `_processWithdrawalCompletion` → `_transferAsset` (L876–883):** The user calls `completeWithdrawal`, which internally calls `_transferAsset(asset, user, request.expectedAssetAmount)`. For ETH, this executes `(bool sent,) = payable(to).call{ value: amount }("")`. If `to` is a contract without a `receive()` or `fallback()` function, this call returns `false`, and the function reverts with `EthTransferFailed`.

Because the entire Step 3 transaction reverts, all state mutations within it are rolled back — the withdrawal request record is restored, and `unlockedWithdrawalsCount[asset]` is restored. However, the rsETH burn from Step 2 (a prior, committed transaction) is **not** rolled back. The ETH remains in `LRTWithdrawalManager` indefinitely.

**No escape hatch exists:**
- `completeWithdrawalForUser` (L192–204) is operator-gated but still calls `_processWithdrawalCompletion(asset, user, ...)`, which calls `_transferAsset` to the same non-receivable `user` address — it will also always revert.
- `sweepRemainingAssets` (L395–413) checks `hasUnlockedWithdrawals(asset)` at L403, which returns `true` as long as `unlockedWithdrawalsCount[asset] > 0`. The stuck withdrawal keeps this count positive, permanently blocking the sweep.

## Impact Explanation
**Critical — Permanent freezing of funds.** Once `unlockQueue` burns the rsETH (Step 2), the user's claim is represented solely by the withdrawal request record. If `completeWithdrawal` always reverts for that user (because their contract cannot receive ETH), both the rsETH (burned) and the ETH (locked in the manager) are permanently unrecoverable without a contract upgrade. There is no alternative redemption path for the affected user, and no admin function can forcibly redirect the ETH to a different address or cancel the stuck request.

## Likelihood Explanation
**Low-Medium.** The affected user must be a smart contract that holds rsETH, calls `initiateWithdrawal` for the ETH asset, and does not implement a `payable` `receive()` or `fallback()` function. This is a realistic scenario for protocol integrators, yield aggregators, or vaults that interact with the withdrawal manager programmatically without anticipating ETH receipt. The condition is not exotic and mirrors the class of contracts that caused the Ignite bug referenced in the submission. The scenario requires no attacker — it is self-inflicted by the integrating contract's design.

## Recommendation
Add a zero-value ETH receivability check inside `initiateWithdrawal` when `asset == LRTConstants.ETH_TOKEN`, placed **before** the rsETH `safeTransferFrom` so that no state change occurs if the check fails:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    (bool canReceive,) = msg.sender.call("");
    if (!canReceive) revert RecipientCannotReceiveETH();
}
```

This is safe because `initiateWithdrawal` already carries the `nonReentrant` modifier (L157), which guards against reentrancy through this external call.

## Proof of Concept
1. Deploy a contract `NoReceiveContract` with no `receive()` or `fallback()` function, holding rsETH.
2. `NoReceiveContract` calls `initiateWithdrawal(ETH_TOKEN, rsETHAmount, "")` — succeeds; rsETH is locked in `LRTWithdrawalManager`.
3. Operator calls `unlockQueue(ETH_TOKEN, ...)` — rsETH is burned at L305, ETH is moved to `LRTWithdrawalManager` at L307.
4. `NoReceiveContract` calls `completeWithdrawal(ETH_TOKEN, "")` — `_transferAsset` at L876 executes `payable(NoReceiveContract).call{value: amount}("")`, which returns `false`; the function reverts with `EthTransferFailed`.
5. The withdrawal request is restored by the revert, but the rsETH burned in Step 3 is not. `unlockedWithdrawalsCount[ETH_TOKEN]` remains `> 0`, blocking `sweepRemainingAssets`.
6. Repeat Step 4 indefinitely — the result is always the same revert. ETH is permanently locked.

Foundry test skeleton:
```solidity
contract NoReceiveContract {
    // No receive() or fallback()
    function doInitiate(address wm, address rsETH, uint256 amount) external {
        IERC20(rsETH).approve(wm, amount);
        ILRTWithdrawalManager(wm).initiateWithdrawal(ETH_TOKEN, amount, "");
    }
    function doComplete(address wm) external {
        ILRTWithdrawalManager(wm).completeWithdrawal(ETH_TOKEN, "");
    }
}

function test_permanentFreeze() public fork {
    NoReceiveContract nrc = new NoReceiveContract();
    deal(address(rsETH), address(nrc), 1 ether);
    nrc.doInitiate(address(withdrawalManager), address(rsETH), 1 ether);
    // operator unlocks
    vm.prank(operator);
    withdrawalManager.unlockQueue(ETH_TOKEN, type(uint256).max, ...);
    // advance blocks past delay
    vm.roll(block.number + withdrawalManager.withdrawalDelayBlocks() + 1);
    // complete always reverts
    vm.expectRevert(ILRTWithdrawalManager.EthTransferFailed.selector);
    nrc.doComplete(address(withdrawalManager));
    // ETH locked, sweep blocked
    assertTrue(withdrawalManager.hasUnlockedWithdrawals(ETH_TOKEN));
}
```