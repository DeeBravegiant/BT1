Audit Report

## Title
ETH Withdrawal Permanently Frozen When Recipient Contract Cannot Receive ETH - (File: contracts/LRTWithdrawalManager.sol)

## Summary

`LRTWithdrawalManager._transferAsset()` pushes native ETH to the withdrawal recipient via `.call{value:}` and unconditionally reverts on failure. If the recipient is a smart contract that cannot receive ETH, `completeWithdrawal` will always revert. Because rsETH is burned in a prior, separate `unlockQueue` transaction, the burn is never rolled back, leaving the user's ETH permanently frozen in the withdrawal manager with no recovery path.

## Finding Description

The withdrawal lifecycle spans two separate transactions:

**Transaction 1 — `unlockQueue`** (lines 301–307):
```solidity
(rsETHBurned, assetAmountUnlocked) = _unlockWithdrawalRequests(...);
if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
unstakingVault.redeem(asset, assetAmountUnlocked);
```
rsETH is irreversibly burned and ETH is redeemed into the withdrawal manager. This transaction is complete and final.

**Transaction 2 — `completeWithdrawal` → `_processWithdrawalCompletion`** (lines 699–738):
The function pops the nonce, deletes the request, decrements `unlockedWithdrawalsCount`, then calls:
```solidity
_transferAsset(asset, user, request.expectedAssetAmount);
```

**`_transferAsset`** (lines 876–883):
```solidity
function _transferAsset(address asset, address to, uint256 amount) internal {
    if (asset == LRTConstants.ETH_TOKEN) {
        (bool sent,) = payable(to).call{ value: amount }("");
        if (!sent) revert EthTransferFailed();
    } else {
        IERC20(asset).safeTransfer(to, amount);
    }
}
```

If `to` is a contract without `receive()`/`fallback()`, `.call{value:}` returns `sent = false` and `EthTransferFailed` is thrown. The entire Transaction 2 reverts, restoring the withdrawal request state. However, Transaction 1 (the burn) is in a committed block and is never rolled back.

Every subsequent call to `completeWithdrawal` or `completeWithdrawalForUser` (line 202, which also calls `_processWithdrawalCompletion(asset, user, referralId)`) hits the same revert. The ETH sits in the withdrawal manager indefinitely. The `sweepRemainingAssets` admin function is blocked while `unlockedWithdrawalsCount[asset] > 0`, which remains nonzero because the decrement at line 717 is always reverted along with the failed transfer.

## Impact Explanation

**Critical — Permanent freezing of funds.**

After `unlockQueue` commits:
- The user's rsETH is irreversibly burned from the withdrawal manager.
- The corresponding ETH is held in the withdrawal manager.
- Both `completeWithdrawal` and `completeWithdrawalForUser` permanently revert for this user.
- No admin sweep or per-user recovery function exists.

The user loses their full rsETH value with no recourse. This matches the allowed impact: *Critical — Permanent freezing of funds*.

## Likelihood Explanation

**Medium.** Smart contracts routinely hold and manage rsETH — DeFi vaults, multisigs, aggregators, and proxy contracts are common depositors. Many such contracts intentionally omit `receive()` (e.g., pure ERC20 vaults, Gnosis Safe modules, proxy contracts with no ETH handling). No privileged access is required; any depositor can trigger this path by initiating an ETH withdrawal from a non-ETH-receiving contract address. The path is deterministic and repeatable.

## Recommendation

Replace the push-ETH pattern with a pull-payment model, or wrap ETH to WETH before delivery so the transfer is always an ERC20 `safeTransfer`. At minimum, store the ETH in a per-user claimable mapping on failure rather than reverting:

```solidity
(bool sent,) = payable(to).call{ value: amount }("");
if (!sent) {
    pendingEthClaims[to] += amount; // allow user to pull later
}
```

A separate `claimEth()` function would let the user pull their ETH from a different address or after adding ETH-receive capability.

## Proof of Concept

1. Deploy `Victim` — a contract with no `receive()` function that holds rsETH.
2. `Victim` calls `LRTWithdrawalManager.initiateWithdrawal(ETH_TOKEN, rsETHAmount, "")`.
3. Operator calls `unlockQueue(ETH_TOKEN, ...)` — rsETH is burned at line 305, ETH redeemed at line 307. This transaction commits.
4. `Victim` calls `completeWithdrawal(ETH_TOKEN, "")` → `_processWithdrawalCompletion` → `_transferAsset(ETH_TOKEN, Victim, amount)` → `.call{value:}` returns `false` → `revert EthTransferFailed()`. Transaction reverts.
5. Step 4 reverts identically on every retry. rsETH is gone. ETH is permanently stuck in the withdrawal manager.

**Foundry test sketch:**
```solidity
contract NoReceive { /* no receive() */ }

function test_permanentFreeze() public {
    NoReceive victim = new NoReceive();
    // fund victim with rsETH, initiate withdrawal
    vm.prank(address(victim));
    withdrawalManager.initiateWithdrawal(ETH_TOKEN, rsETHAmount, "");
    // operator unlocks
    withdrawalManager.unlockQueue(ETH_TOKEN, ...);
    // attempt completion — must revert every time
    vm.prank(address(victim));
    vm.expectRevert(LRTWithdrawalManager.EthTransferFailed.selector);
    withdrawalManager.completeWithdrawal(ETH_TOKEN, "");
    // ETH balance of withdrawalManager remains nonzero; rsETH supply decreased
    assertGt(address(withdrawalManager).balance, 0);
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** contracts/LRTWithdrawalManager.sol (L712-734)
```text
        delete withdrawalRequests[requestId];

        // Check that the withdrawal delay has passed since the request's initiation.
        if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();

        unlockedWithdrawalsCount[asset]--;

        // If Aave integration is enabled and asset is ETH, withdraw from Aave if needed
        if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN) {
            uint256 contractBalance = address(this).balance;
            if (contractBalance < request.expectedAssetAmount) {
                uint256 amountNeeded = request.expectedAssetAmount - contractBalance;
                _withdrawFromAave(amountNeeded);

                // Verify we have sufficient balance after withdrawal
                uint256 balanceAfter = address(this).balance;
                if (balanceAfter < request.expectedAssetAmount) {
                    revert InsufficientLiquidityForWithdrawal();
                }
            }
        }

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
