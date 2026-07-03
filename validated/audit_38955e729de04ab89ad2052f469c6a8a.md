Audit Report

## Title
aWETH Direct Transfer Causes Silent Zero-Return in `_withdrawFromAave`, Freezing ETH Withdrawals — (File: `contracts/LRTWithdrawalManager.sol`)

## Summary
When `totalETHDepositedToAave == 0` but `aaveAWETH.balanceOf(address(this)) > 0` (achieved by an attacker directly transferring aWETH to the contract), `_withdrawFromAave` computes `withdrawablePrincipal = min(aaveBalance, 0) = 0` and silently returns `0`. The calling code in `_processWithdrawalCompletion` then reverts with `InsufficientLiquidityForWithdrawal`, freezing all pending ETH withdrawal completions that require Aave liquidity until operator intervention.

## Finding Description
`totalETHDepositedToAave` is initialized to `0` and is only incremented inside `_depositToAave`:

```solidity
// L894-901
function _depositToAave(uint256 amount) internal {
    if (amount == 0) return;
    aaveWETHGateway.depositETH{ value: amount }(aavePool, address(this), 0);
    totalETHDepositedToAave += amount;
    emit ETHDepositedToAave(amount, totalETHDepositedToAave);
}
```

`_withdrawFromAave` caps withdrawable amount at `min(aaveBalance, totalETHDepositedToAave)`:

```solidity
// L912-915
uint256 withdrawablePrincipal = aaveBalance < totalETHDepositedToAave ? aaveBalance : totalETHDepositedToAave;
withdrawnAmount = amount > withdrawablePrincipal ? withdrawablePrincipal : amount;
if (withdrawnAmount == 0) return 0;
```

When `totalETHDepositedToAave == 0` and `aaveBalance > 0`:
- `aaveBalance < 0` is false → `withdrawablePrincipal = totalETHDepositedToAave = 0`
- `withdrawnAmount = 0` → function returns `0` silently (no revert, because `aaveBalance != 0` passes the guard at L909)

The caller at L719–731 then checks `address(this).balance` post-withdrawal and reverts:

```solidity
uint256 balanceAfter = address(this).balance;
if (balanceAfter < request.expectedAssetAmount) {
    revert InsufficientLiquidityForWithdrawal();
}
```

aWETH is a standard ERC-20; any holder can `transfer` it directly to `LRTWithdrawalManager` without going through `_depositToAave`, leaving the accounting variable at `0` while the actual balance is nonzero. The contract address is deterministic (UUPS proxy), so the attacker can pre-compute it.

After operator calls `collectInterestToTreasury()`, `_collectInterestToTreasury` treats the entire donated balance as interest (`aaveBalance - principal = aaveBalance - 0 = aaveBalance`) and sweeps it to treasury. This drops `aaveBalance` to `0`, causing subsequent `_withdrawFromAave` calls to revert with `InsufficientAaveBalance` instead — the freeze continues until the operator re-deposits ETH or disables the integration.

## Impact Explanation
**Medium — Temporary freezing of funds.**

All ETH withdrawal completions where `address(this).balance < request.expectedAssetAmount` revert with `InsufficientLiquidityForWithdrawal` for the duration of the accounting mismatch. User funds are not lost but are inaccessible until operator intervention (`depositIdleETHToAave`, `setAaveIntegrationEnabled(false)`, or re-deposit). This matches the allowed impact class "Medium. Temporary freezing of funds."

## Likelihood Explanation
**Low.** The attacker must spend real aWETH (a liquid, valuable asset) with no direct financial gain — the donated tokens are swept to the protocol treasury. The attack window is widest when `totalETHDepositedToAave == 0`: immediately after Aave integration is configured/enabled, or after a full emergency withdrawal resets the counter to `0`. Operator intervention resolves the freeze, so the DOS is temporary but repeatable at cost.

## Recommendation
When enabling the Aave integration (in `configureAaveIntegration` or `setAaveIntegrationEnabled(true)`), initialize `totalETHDepositedToAave` to match any pre-existing aWETH balance held by the contract:

```solidity
// In configureAaveIntegration / setAaveIntegrationEnabled(true):
uint256 existingBalance = IAToken(aaveAWETH_).balanceOf(address(this));
if (existingBalance > 0) {
    totalETHDepositedToAave = existingBalance;
}
```

Alternatively, modify `_withdrawFromAave` to use `aaveBalance` directly as the withdrawable amount when `totalETHDepositedToAave == 0` but `aaveBalance > 0`, or add a dedicated admin function to reconcile the accounting variable against the actual aWETH balance.

## Proof of Concept
1. Aave integration is configured and enabled; `totalETHDepositedToAave == 0` (no ETH deposited yet via `_depositToAave`).
2. Attacker calls `aaveAWETH.transfer(address(lrtWithdrawalManager), 1 ether)`.
3. State: `aaveAWETH.balanceOf(lrtWithdrawalManager) == 1 ether`; `totalETHDepositedToAave == 0`; `address(lrtWithdrawalManager).balance == 0`.
4. A user calls `completeWithdrawal(ETH, ...)` for a pending request of `0.5 ether`.
5. `_processWithdrawalCompletion` enters the Aave branch: `contractBalance (0) < expectedAssetAmount (0.5 ether)` → calls `_withdrawFromAave(0.5 ether)`.
6. Inside `_withdrawFromAave`: `aaveBalance = 1 ether` (passes the `== 0` guard); `withdrawablePrincipal = min(1 ether, 0) = 0`; `withdrawnAmount = 0`; returns `0` silently.
7. `balanceAfter = 0 < 0.5 ether` → reverts `InsufficientLiquidityForWithdrawal`.
8. All ETH withdrawal completions requiring Aave liquidity are frozen until operator intervention.

**Foundry test sketch:**
```solidity
function test_aWETHDonationFreezesWithdrawals() public {
    // Setup: enable Aave integration, totalETHDepositedToAave == 0
    vm.prank(attacker);
    aaveAWETH.transfer(address(withdrawalManager), 1 ether);

    // Queue and unlock a user ETH withdrawal request for 0.5 ether
    // (setup omitted for brevity)

    vm.expectRevert(ILRTWithdrawalManager.InsufficientLiquidityForWithdrawal.selector);
    vm.prank(user);
    withdrawalManager.completeWithdrawal(LRTConstants.ETH_TOKEN, 0);
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L64-65)
```text
    bool public isAaveIntegrationEnabled;
    uint256 public totalETHDepositedToAave;
```

**File:** contracts/LRTWithdrawalManager.sol (L719-732)
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
        }
```

**File:** contracts/LRTWithdrawalManager.sol (L894-901)
```text
    function _depositToAave(uint256 amount) internal {
        if (amount == 0) return;

        aaveWETHGateway.depositETH{ value: amount }(aavePool, address(this), 0);
        totalETHDepositedToAave += amount;

        emit ETHDepositedToAave(amount, totalETHDepositedToAave);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L905-921)
```text
    function _withdrawFromAave(uint256 amount) internal returns (uint256 withdrawnAmount) {
        if (amount == 0) return 0;

        uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
        if (aaveBalance == 0) revert InsufficientAaveBalance();

        // Only withdraw up to the principal amount (don't use accrued interest for user withdrawals)
        uint256 withdrawablePrincipal = aaveBalance < totalETHDepositedToAave ? aaveBalance : totalETHDepositedToAave;

        withdrawnAmount = amount > withdrawablePrincipal ? withdrawablePrincipal : amount;
        if (withdrawnAmount == 0) return 0;

        aaveWETHGateway.withdrawETH(aavePool, withdrawnAmount, address(this));
        totalETHDepositedToAave -= withdrawnAmount;

        emit ETHWithdrawnFromAave(withdrawnAmount, totalETHDepositedToAave);
    }
```
