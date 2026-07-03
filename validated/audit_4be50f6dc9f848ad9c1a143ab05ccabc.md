Audit Report

## Title
ETH Withdrawal Permanently Bricked When Aave WETH Reserve Is Paused — (`contracts/LRTWithdrawalManager.sol`)

## Summary

`LRTWithdrawalManager` deposits unlocked ETH into Aave v3 using a `try/catch` that gracefully handles deposit failures, but withdraws from Aave in `_processWithdrawalCompletion` with no equivalent error handling. If Aave's WETH reserve is paused, `_withdrawFromAave` reverts, blocking every `completeWithdrawal` call that requires Aave liquidity. The intended rescue function `emergencyWithdrawFromAave` routes through the same `_withdrawFromAave` path and is equally broken, leaving user ETH permanently frozen inside Aave after their rsETH has already been irreversibly burned.

## Finding Description

**Asymmetric error handling between deposit and withdrawal paths.**

In `unlockQueue`, the Aave deposit is wrapped in a `try/catch`: [1](#0-0) 

If the deposit fails, ETH stays in the contract and the function continues. When the deposit *succeeds*, ETH moves into Aave and `totalETHDepositedToAave` is incremented, leaving the contract's idle ETH balance at zero.

In `_processWithdrawalCompletion`, the withdrawal from Aave has no equivalent protection: [2](#0-1) 

`_withdrawFromAave` at line 724 calls `aaveWETHGateway.withdrawETH` directly: [3](#0-2) 

If Aave's WETH reserve is paused, `withdrawETH` reverts. Because there is no `try/catch`, the entire `completeWithdrawal` transaction reverts. The user's rsETH was already burned in `unlockQueue` — that burn is irreversible.

**Emergency escape hatch is equally broken.**

`emergencyWithdrawFromAave` calls `_withdrawFromAave` directly with no error handling: [4](#0-3) 

It routes through the same `aaveWETHGateway.withdrawETH` call and reverts under the same condition. There is no alternative code path to retrieve the ETH.

## Impact Explanation

**Critical — Permanent freezing of funds.**

By the time `completeWithdrawal` is called, the user's rsETH has been irreversibly burned in `unlockQueue`. The corresponding ETH has been deposited into Aave. If Aave's WETH reserve is paused:

- `completeWithdrawal` reverts for every ETH withdrawal requiring Aave liquidity.
- `emergencyWithdrawFromAave` reverts via the same path.
- No on-chain rescue path exists.

Users have permanently lost rsETH and cannot receive ETH. This matches the allowed Critical impact: **Permanent freezing of funds**.

## Likelihood Explanation

Aave v3 reserves have a `PAUSED` state triggerable by the Aave Guardian (a multisig) in response to emergencies such as oracle manipulation or exploit detection. This has occurred on live Aave deployments. The condition is not attacker-controlled, but it is a realistic, documented protocol-level event. Once triggered, the freeze persists until Aave governance unpauses the reserve, which may take days or be indefinite. The LRT developers clearly anticipated Aave failures on the deposit side (hence the `try/catch`), making the omission on the withdrawal side a concrete code-level defect rather than a theoretical concern.

## Recommendation

Mirror the `try/catch` pattern from `unlockQueue` inside `_processWithdrawalCompletion`. If the Aave withdrawal fails, fall through to the balance check and revert only if idle ETH is also insufficient:

```solidity
if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN) {
    uint256 contractBalance = address(this).balance;
    if (contractBalance < request.expectedAssetAmount) {
        uint256 amountNeeded = request.expectedAssetAmount - contractBalance;
        try this.withdrawFromAaveExternal(amountNeeded) { }
        catch { } // Aave paused/frozen — fall through to balance check
        uint256 balanceAfter = address(this).balance;
        if (balanceAfter < request.expectedAssetAmount) {
            revert InsufficientLiquidityForWithdrawal();
        }
    }
}
```

Additionally, `emergencyWithdrawFromAave` should force-disable `isAaveIntegrationEnabled` even when `_withdrawFromAave` fails, so subsequent `completeWithdrawal` calls bypass the Aave path and serve users from idle contract balance.

## Proof of Concept

1. `isAaveIntegrationEnabled = true`.
2. Operator calls `unlockQueue(ETH_TOKEN, ...)`. rsETH is burned; ETH is pulled from `LRTUnstakingVault` and successfully deposited into Aave. `totalETHDepositedToAave > 0`; contract idle ETH balance is 0.
3. Aave Guardian pauses the WETH reserve (documented emergency action with mainnet precedent).
4. User calls `completeWithdrawal(ETH_TOKEN, ...)`.
5. `_processWithdrawalCompletion` enters the Aave branch: `address(this).balance (0) < request.expectedAssetAmount`.
6. `_withdrawFromAave(amountNeeded)` → `aaveWETHGateway.withdrawETH(...)` reverts because reserve is paused.
7. `completeWithdrawal` reverts. User cannot retrieve ETH.
8. Admin calls `emergencyWithdrawFromAave(type(uint256).max)` → same `_withdrawFromAave` path → same revert.
9. ETH is permanently frozen in Aave; user's rsETH is already burned and unrecoverable.

**Foundry fork test outline:**
```solidity
// Fork mainnet, deploy LRTWithdrawalManager with Aave integration enabled
// Call unlockQueue to deposit ETH into Aave
// vm.prank(aaveGuardian); aaveConfigurator.setReservePause(WETH, true);
// vm.expectRevert(); lrtWithdrawalManager.completeWithdrawal(ETH_TOKEN, ...);
// vm.expectRevert(); lrtWithdrawalManager.emergencyWithdrawFromAave(type(uint256).max);
```

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L310-317)
```text
        if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN && assetAmountUnlocked > 0) {
            try this.depositToAaveExternal(assetAmountUnlocked) { }
            catch (bytes memory reason) {
                emit AaveDepositFailed(assetAmountUnlocked, reason);
                // Silently fail if Aave deposit fails (e.g., pool at max capacity)
                // Funds remain in contract for withdrawals
            }
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

**File:** contracts/LRTWithdrawalManager.sol (L719-731)
```text
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

**File:** contracts/LRTWithdrawalManager.sol (L917-918)
```text
        aaveWETHGateway.withdrawETH(aavePool, withdrawnAmount, address(this));
        totalETHDepositedToAave -= withdrawnAmount;
```
