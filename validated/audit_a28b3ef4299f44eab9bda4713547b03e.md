### Title
Aave Illiquidity Permanently Blocks `sweepRemainingAssets` via Unrecoverable `unlockedWithdrawalsCount` — (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

When Aave integration is enabled for ETH withdrawals, a revert inside `_processWithdrawalCompletion` rolls back the `unlockedWithdrawalsCount[asset]--` decrement that already executed. Because every admin recovery path that could re-enable normal withdrawals also depends on Aave liquidity, the counter can never reach zero, permanently blocking `sweepRemainingAssets` and trapping any residual LST/ETH balance in the contract until Aave liquidity is externally restored.

---

### Finding Description

**Root cause — state rollback on revert**

`_processWithdrawalCompletion` decrements `unlockedWithdrawalsCount[asset]` at line 717 **before** the Aave liquidity check at lines 720–731:

```solidity
// line 717
unlockedWithdrawalsCount[asset]--;

// lines 720-731
if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN) {
    uint256 contractBalance = address(this).balance;
    if (contractBalance < request.expectedAssetAmount) {
        uint256 amountNeeded = request.expectedAssetAmount - contractBalance;
        _withdrawFromAave(amountNeeded);          // can revert

        uint256 balanceAfter = address(this).balance;
        if (balanceAfter < request.expectedAssetAmount) {
            revert InsufficientLiquidityForWithdrawal();   // line 729
        }
    }
}
``` [1](#0-0) 

When `InsufficientLiquidityForWithdrawal` is thrown at line 729, the EVM reverts the entire transaction, including the decrement at line 717. `unlockedWithdrawalsCount[asset]` is never reduced.

**How ETH ends up fully in Aave**

`unlockQueue` auto-deposits redeemed ETH into Aave (lines 309–317) via a try/catch that silently swallows deposit failures. Once the deposit succeeds, `address(this).balance` is 0, so every subsequent `completeWithdrawal` call will always enter the Aave withdrawal branch. [2](#0-1) 

**`sweepRemainingAssets` is permanently gated**

`sweepRemainingAssets` hard-reverts with `PendingWithdrawalsExist` whenever `hasUnlockedWithdrawals(asset)` returns true, which it does as long as `unlockedWithdrawalsCount[asset] > 0`: [3](#0-2) [4](#0-3) 

**No admin escape hatch**

Every admin path that could break the deadlock also depends on Aave liquidity:

| Function | Why it fails |
|---|---|
| `setAaveIntegrationEnabled(false)` | Calls `_withdrawFromAave(aaveBalance)` (line 495); reverts if Aave pool is illiquid |
| `emergencyWithdrawFromAave` | Also calls `_withdrawFromAave` (line 560); same failure |
| `completeWithdrawal` / `completeWithdrawalForUser` | Both call `_processWithdrawalCompletion`; same revert path | [5](#0-4) [6](#0-5) 

There is no function that force-sets `isAaveIntegrationEnabled = false` without first draining Aave, and no function that directly decrements `unlockedWithdrawalsCount` or bypasses the `hasUnlockedWithdrawals` gate in `sweepRemainingAssets`.

---

### Impact Explanation

Any residual LST or ETH balance held by `LRTWithdrawalManager` (e.g., rounding dust, over-funded unlocks, or assets sent directly) is inaccessible to the operator via `sweepRemainingAssets` for the entire duration of Aave illiquidity. This matches **Medium — Temporary freezing of funds**.

---

### Likelihood Explanation

Aave v3 WETH utilization regularly spikes to 100% during market stress (e.g., ETH price crashes, mass liquidations). The scenario requires only:
1. Aave integration enabled (operator action, already in scope).
2. At least one unlocked ETH withdrawal (normal operational state).
3. Aave WETH utilization at 100% (realistic market condition, not an attack).

No attacker action is required; this is a passive operational failure mode.

---

### Recommendation

Two independent fixes, either sufficient:

1. **Move the decrement after the Aave withdrawal succeeds.** Place `unlockedWithdrawalsCount[asset]--` after the Aave liquidity check block (after line 731), so a revert never rolls back a premature decrement.

2. **Add a force-disable path for Aave.** Provide a privileged function (e.g., `forceDisableAaveIntegration`) that sets `isAaveIntegrationEnabled = false` without attempting to withdraw from Aave, allowing `completeWithdrawal` to proceed using only the contract's native ETH balance.

---

### Proof of Concept

```solidity
// Fork test (Foundry) — Aave WETH pool at 100% utilization
function test_sweepBlockedByAaveIlliquidity() public {
    // 1. Users initiate ETH withdrawals
    vm.prank(user1); withdrawalManager.initiateWithdrawal(ETH, 1 ether, "");
    vm.prank(user2); withdrawalManager.initiateWithdrawal(ETH, 1 ether, "");

    // 2. Operator unlocks queue → unlockedWithdrawalsCount[ETH] = 2, ETH deposited to Aave
    vm.roll(block.number + withdrawalDelayBlocks + 1);
    vm.prank(operator); withdrawalManager.unlockQueue(ETH, type(uint256).max, ...);
    assertEq(withdrawalManager.unlockedWithdrawalsCount(ETH), 2);
    assertEq(address(withdrawalManager).balance, 0); // all in Aave

    // 3. Drain Aave WETH liquidity to 0 (fork: borrow all WETH from Aave pool)
    _drainAaveLiquidity();

    // 4. completeWithdrawal reverts → decrement rolled back
    vm.prank(user1);
    vm.expectRevert(ILRTWithdrawalManager.InsufficientLiquidityForWithdrawal.selector);
    withdrawalManager.completeWithdrawal(ETH, "");
    assertEq(withdrawalManager.unlockedWithdrawalsCount(ETH), 2); // unchanged

    // 5. setAaveIntegrationEnabled(false) also reverts
    vm.prank(manager);
    vm.expectRevert(); // Aave withdrawETH reverts
    withdrawalManager.setAaveIntegrationEnabled(false);

    // 6. sweepRemainingAssets is permanently blocked
    vm.prank(manager);
    vm.expectRevert(ILRTWithdrawalManager.PendingWithdrawalsExist.selector);
    withdrawalManager.sweepRemainingAssets(ETH);
}
```

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L309-317)
```text
        // If Aave integration is enabled and asset is ETH, deposit to Aave
        if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN && assetAmountUnlocked > 0) {
            try this.depositToAaveExternal(assetAmountUnlocked) { }
            catch (bytes memory reason) {
                emit AaveDepositFailed(assetAmountUnlocked, reason);
                // Silently fail if Aave deposit fails (e.g., pool at max capacity)
                // Funds remain in contract for withdrawals
            }
        }
```

**File:** contracts/LRTWithdrawalManager.sol (L402-403)
```text
        // Check that all withdrawals are completed
        if (hasUnlockedWithdrawals(asset)) revert PendingWithdrawalsExist();
```

**File:** contracts/LRTWithdrawalManager.sol (L486-501)
```text
        if (!enabled) {
            uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
            if (aaveBalance > 0) {
                // First collect any accrued interest to treasury
                _collectInterestToTreasury();

                // Then withdraw remaining principal from Aave back to contract
                aaveBalance = aaveAWETH.balanceOf(address(this));
                if (aaveBalance > 0) {
                    _withdrawFromAave(aaveBalance);
                }
            }

            // Revoke approval for aWETH token to Aave WETH Gateway
            _revokeApprovalToAaveWETHGateway();
        }
```

**File:** contracts/LRTWithdrawalManager.sol (L551-563)
```text
    function emergencyWithdrawFromAave(uint256 amount) external nonReentrant onlyRole(LRTConstants.PAUSER_ROLE) {
        if (!isAaveIntegrationEnabled) revert AaveIntegrationNotEnabled();

        uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
        if (aaveBalance == 0) revert InsufficientAaveBalance();

        // First collect any accrued interest to treasury
        _collectInterestToTreasury();

        uint256 withdrawnAmount = _withdrawFromAave(amount);

        emit EmergencyWithdrawFromAave(withdrawnAmount, address(this));
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L629-631)
```text
    function hasUnlockedWithdrawals(address asset) public view returns (bool hasUnlocked) {
        return unlockedWithdrawalsCount[asset] > 0;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L717-731)
```text
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
```
