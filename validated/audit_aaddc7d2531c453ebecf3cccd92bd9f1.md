Audit Report

## Title
ETH Withdrawal Permanently Frozen When Recipient Contract Cannot Receive ETH - (`contracts/LRTWithdrawalManager.sol`)

## Summary
`LRTWithdrawalManager` burns rsETH in `unlockQueue` (an operator transaction) before the user calls `completeWithdrawal`. If the recipient address is a smart contract that rejects ETH, `_transferAsset` reverts with `EthTransferFailed`, rolling back only the completion-step state changes — but the rsETH burn from `unlockQueue` is already finalized in a prior transaction and cannot be undone. The corresponding ETH remains locked in the contract with no admin escape hatch to redirect it.

## Finding Description
The withdrawal lifecycle spans two separate transactions:

**Step 1 — `unlockQueue` (operator):** rsETH is burned irreversibly via `IRSETH.burnFrom` and ETH is pulled from the unstaking vault into `LRTWithdrawalManager`. [1](#0-0) 

**Step 2 — `completeWithdrawal` / `completeWithdrawalForUser`:** Both call `_processWithdrawalCompletion`, which ends with a push-payment ETH transfer to the user. [2](#0-1) 

The ETH transfer uses a raw `.call`: [3](#0-2) 

If `to` is a contract with no `receive()`/`fallback()`, or one whose `receive()` conditionally reverts (e.g., a paused vault, an upgraded multisig, a DAO contract), `sent == false` and the entire `_processWithdrawalCompletion` call reverts. All state changes within that call — the `popFront()`, `delete withdrawalRequests[requestId]`, and `unlockedWithdrawalsCount[asset]--` — are rolled back. The withdrawal request remains in the unlocked queue indefinitely.

`completeWithdrawalForUser` (operator-initiated) calls the identical `_processWithdrawalCompletion` path with the same `user` address and hits the same revert — there is no parameter to redirect ETH to an alternate address. [4](#0-3) 

`sweepRemainingAssets` cannot recover the locked ETH because it requires `hasUnlockedWithdrawals(asset) == false`, but `unlockedWithdrawalsCount[asset]` is never decremented when `completeWithdrawal` reverts: [5](#0-4) 

There is no admin function to forcibly redirect a stuck ETH withdrawal to an alternate address.

## Impact Explanation
**Critical — Permanent freezing of funds.** The user's rsETH is burned in `unlockQueue` (finalized, irreversible). If `completeWithdrawal` always reverts for that user, the corresponding ETH is locked in `LRTWithdrawalManager` indefinitely. The user loses both their rsETH and their ETH with no on-chain recovery mechanism. This matches the allowed Critical impact: *Permanent freezing of funds*.

## Likelihood Explanation
**Low-to-medium.** Smart contract wallets (Gnosis Safe, DAO treasuries, yield aggregators) are common DeFi participants. A contract without a `receive()` function, or whose `receive()` reverts under certain conditions (e.g., after a contract upgrade, when the contract is paused, or due to a gas-limited callback), would trigger this. Critically, the contract's ETH-receiving capability can change *between* `initiateWithdrawal` and `completeWithdrawal`, which are separated by at least `withdrawalDelayBlocks` (~8 days): [6](#0-5) 
The user may not anticipate this at initiation time, making this a realistic scenario rather than a pure user mistake.

## Recommendation
Replace the push-payment pattern with a pull-payment pattern for ETH withdrawals. Instead of calling `payable(to).call{value: amount}("")` inside `_processWithdrawalCompletion`, record the owed ETH in a `pendingETH[user]` mapping and provide a separate `claimETH()` function the user can call from any address they control. Alternatively, add an admin/operator function that allows redirecting a stuck ETH withdrawal to an alternate address provided by the original user (with appropriate authorization checks).

## Proof of Concept
1. A DAO treasury contract (no `receive()`) calls `initiateWithdrawal(ETH_TOKEN, rsETHAmount, "")`. rsETH is escrowed in `LRTWithdrawalManager`.
2. After the delay, the operator calls `unlockQueue(ETH_TOKEN, ...)`. rsETH is burned via `burnFrom` at L305. ETH is pulled from the unstaking vault. `unlockedWithdrawalsCount[ETH_TOKEN]` is incremented.
3. The DAO calls `completeWithdrawal(ETH_TOKEN, "")`. Inside `_processWithdrawalCompletion`, `_transferAsset` executes `payable(daoAddress).call{value: amount}("")`. The DAO has no `receive()`, so `sent == false`. `EthTransferFailed` is reverted. All state changes in step 3 are rolled back.
4. The operator calls `completeWithdrawalForUser(ETH_TOKEN, daoAddress, "")` — same revert, same rollback.
5. `sweepRemainingAssets` reverts with `PendingWithdrawalsExist` because `unlockedWithdrawalsCount[ETH_TOKEN] > 0`.
6. The DAO's rsETH is permanently burned. The ETH is permanently locked in `LRTWithdrawalManager`.

**Foundry test sketch:**
```solidity
// NoReceiveContract: no receive() or fallback()
contract NoReceiveContract { }

function test_ethWithdrawalFrozen() public {
    NoReceiveContract dao = new NoReceiveContract();
    // 1. dao initiates withdrawal (prank as dao)
    vm.prank(address(dao));
    withdrawalManager.initiateWithdrawal(ETH_TOKEN, rsETHAmount, "");
    // 2. operator unlocks queue (burns rsETH, pulls ETH)
    vm.roll(block.number + withdrawalDelayBlocks + 1);
    vm.prank(operator);
    withdrawalManager.unlockQueue(ETH_TOKEN, type(uint256).max, ...);
    // rsETH is now burned — verify rsETH balance of withdrawalManager is 0
    assertEq(rsETH.balanceOf(address(withdrawalManager)), 0);
    // 3. completeWithdrawal reverts
    vm.prank(address(dao));
    vm.expectRevert(LRTWithdrawalManager.EthTransferFailed.selector);
    withdrawalManager.completeWithdrawal(ETH_TOKEN, "");
    // 4. ETH remains locked, unlockedWithdrawalsCount still > 0
    assertTrue(withdrawalManager.hasUnlockedWithdrawals(ETH_TOKEN));
    // 5. sweepRemainingAssets also reverts
    vm.prank(manager);
    vm.expectRevert(LRTWithdrawalManager.PendingWithdrawalsExist.selector);
    withdrawalManager.sweepRemainingAssets(ETH_TOKEN);
}
```

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L94-94)
```text
        withdrawalDelayBlocks = 8 days / 12 seconds;
```

**File:** contracts/LRTWithdrawalManager.sol (L192-203)
```text
    function completeWithdrawalForUser(
        address asset,
        address user,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlyLRTOperator
    {
        _processWithdrawalCompletion(asset, user, referralId);
        emit AssetWithdrawalCompletedBy(msg.sender);
```

**File:** contracts/LRTWithdrawalManager.sol (L305-307)
```text
        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTWithdrawalManager.sol (L395-413)
```text
    function sweepRemainingAssets(address asset)
        external
        nonReentrant
        onlySupportedAsset(asset)
        onlyLRTManager
        returns (uint256 transferredAmount)
    {
        // Check that all withdrawals are completed
        if (hasUnlockedWithdrawals(asset)) revert PendingWithdrawalsExist();

        uint256 balance = _getAssetBalance(asset);
        if (balance == 0) revert AmountMustBeGreaterThanZero();

        // Transfer to treasury
        address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        _transferAsset(asset, treasury, balance);

        emit RemainingAssetsSwept(asset, balance, treasury);
        return balance;
```

**File:** contracts/LRTWithdrawalManager.sol (L734-734)
```text
        _transferAsset(asset, user, request.expectedAssetAmount);
```

**File:** contracts/LRTWithdrawalManager.sol (L876-883)
```text
    function _transferAsset(address asset, address to, uint256 amount) internal {
        if (asset == LRTConstants.ETH_TOKEN) {
            (bool sent,) = payable(to).call{ value: amount }("");
            if (!sent) revert EthTransferFailed();
        } else {
            IERC20(asset).safeTransfer(to, amount);
        }
    }
```
