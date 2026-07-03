I have all the code needed. Let me analyze the exact execution path carefully.

### Title
Accrued Aave Interest Permanently Blocks ETH Withdrawal Completion When `totalETHDepositedToAave` Falls Below `request.expectedAssetAmount` - (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

`_withdrawFromAave` hard-caps the amount it can withdraw at `min(aaveAWETH.balanceOf, totalETHDepositedToAave)`, intentionally excluding accrued interest. When prior completed withdrawals have reduced `totalETHDepositedToAave` below a pending request's `expectedAssetAmount`, but the actual aWETH balance (principal + interest) is still sufficient to cover it, `_processWithdrawalCompletion` will always revert with `InsufficientLiquidityForWithdrawal`. The user's withdrawal request is stuck indefinitely with no permissionless recovery path.

---

### Finding Description

**`_withdrawFromAave` (lines 905–921):**

```solidity
uint256 withdrawablePrincipal = aaveBalance < totalETHDepositedToAave
    ? aaveBalance
    : totalETHDepositedToAave;                          // caps at principal tracker

withdrawnAmount = amount > withdrawablePrincipal ? withdrawablePrincipal : amount;
```

The cap is `min(aaveAWETH.balanceOf, totalETHDepositedToAave)`. Interest that has accrued on top of the principal is never accessible for user withdrawals.

**`_processWithdrawalCompletion` (lines 719–731):**

```solidity
if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN) {
    uint256 contractBalance = address(this).balance;
    if (contractBalance < request.expectedAssetAmount) {
        uint256 amountNeeded = request.expectedAssetAmount - contractBalance;
        _withdrawFromAave(amountNeeded);

        uint256 balanceAfter = address(this).balance;
        if (balanceAfter < request.expectedAssetAmount) {
            revert InsufficientLiquidityForWithdrawal();   // ← stuck here
        }
    }
}
```

**Concrete state that triggers the bug:**

| Variable | Value |
|---|---|
| `totalETHDepositedToAave` | 5 ETH (reduced by prior withdrawals) |
| `aaveAWETH.balanceOf(this)` | 10 ETH (5 principal + 5 accrued interest) |
| `request.expectedAssetAmount` | 10 ETH (locked in before prior withdrawals) |
| `address(this).balance` | 0 ETH |

Execution of `completeWithdrawal`:
1. `amountNeeded = 10 ETH`
2. `_withdrawFromAave(10)` → `withdrawablePrincipal = min(10, 5) = 5` → withdraws 5 ETH
3. `balanceAfter = 5 ETH < 10 ETH` → **reverts `InsufficientLiquidityForWithdrawal`**

Because the revert rolls back all state changes (including `delete withdrawalRequests[requestId]` and `popFront()`), the request survives but every subsequent call to `completeWithdrawal` hits the same revert.

**No permissionless recovery path exists:**

- `emergencyWithdrawFromAave` calls `_collectInterestToTreasury()` first (sends the 5 ETH interest to treasury, making the situation worse), then `_withdrawFromAave` which is still capped at `totalETHDepositedToAave = 5`.
- `setAaveIntegrationEnabled(false)` also calls `_collectInterestToTreasury()` then `_withdrawFromAave(aaveBalance)` — same result: interest goes to treasury, only 5 ETH principal lands in the contract, user still cannot withdraw 10 ETH.
- `depositIdleETHToAave` requires idle ETH already in the contract.
- The user has no function to call that bypasses the cap.

---

### Impact Explanation

A user whose withdrawal was properly unlocked (the protocol committed to paying `expectedAssetAmount`) cannot complete it. Their rsETH has already been transferred to the contract at `initiateWithdrawal` time. The ETH needed to satisfy the withdrawal exists in Aave as aWETH, but the accounting variable `totalETHDepositedToAave` prevents its retrieval. Without new ETH deposits flowing into the protocol and being deposited to Aave (increasing `totalETHDepositedToAave`), the withdrawal is permanently frozen. This violates the core invariant that an unlocked withdrawal request must always be satisfiable.

**Impact: Critical — Permanent freezing of user ETH.**

---

### Likelihood Explanation

The condition requires:
1. Aave integration is enabled (intended production path).
2. Interest has accrued (guaranteed over time on any non-zero deposit).
3. Prior withdrawals have reduced `totalETHDepositedToAave` below a pending request's `expectedAssetAmount` — a normal operational outcome when multiple users withdraw sequentially.
4. No idle ETH sits in the contract at the time of the stuck user's `completeWithdrawal` call.

All four conditions are routine in normal protocol operation. No attacker action is required; this is a pure accounting bug triggered by ordinary user activity.

---

### Recommendation

When withdrawing for a user, allow `_withdrawFromAave` to use the full `aaveAWETH.balanceOf` (not just `totalETHDepositedToAave`) when the principal tracker is insufficient to cover the request. Adjust `totalETHDepositedToAave` to zero in that case (it cannot go negative). Alternatively, track `assetsCommitted` against the full aWETH balance rather than only the principal, so that interest is considered available for committed withdrawals before being swept to treasury.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Foundry fork test (Ethereum mainnet fork, Aave v3 WETH market)
// Run: forge test --match-test test_withdrawalFrozenByInterestAccrual --fork-url $ETH_RPC_URL -vvvv

contract FrozenWithdrawalPoC is Test {
    LRTWithdrawalManager withdrawalManager; // deployed/configured instance
    address userA;
    address userB;

    function test_withdrawalFrozenByInterestAccrual() public {
        // 1. Setup: 100 ETH deposited to Aave, totalETHDepositedToAave = 100
        // 2. Warp time so Aave accrues 5 ETH interest → aaveAWETH.balanceOf = 105
        vm.warp(block.timestamp + 365 days);

        // 3. UserA queues withdrawal for 10 ETH (expectedAssetAmount = 10e18)
        // 4. UserB queues + completes withdrawal for 95 ETH
        //    → totalETHDepositedToAave = 5, aaveAWETH.balanceOf = 10 (5 principal + 5 interest)

        // 5. UserA tries to complete withdrawal
        vm.prank(userA);
        vm.expectRevert(ILRTWithdrawalManager.InsufficientLiquidityForWithdrawal.selector);
        withdrawalManager.completeWithdrawal(LRTConstants.ETH_TOKEN, "");

        // 6. Confirm aaveAWETH balance IS sufficient but totalETHDepositedToAave is not
        assertGe(withdrawalManager.getAaveBalance(), 10e18);          // 10 ETH in Aave
        assertLt(withdrawalManager.totalETHDepositedToAave(), 10e18); // only 5 ETH principal

        // 7. Confirm request still exists (revert rolled back state)
        (,uint256 expected,,) = withdrawalManager.getUserWithdrawalRequest(
            LRTConstants.ETH_TOKEN, userA, 0
        );
        assertEq(expected, 10e18); // request is alive but permanently uncompletable
    }
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L486-497)
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

**File:** contracts/LRTWithdrawalManager.sol (L945-961)
```text
    function _collectInterestToTreasury() internal returns (uint256 interestAmount) {
        uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
        uint256 principal = totalETHDepositedToAave;

        // Return 0 if no interest or balance is less than principal (accounting for rounding)
        if (aaveBalance <= principal) return 0;

        interestAmount = aaveBalance - principal;

        aaveWETHGateway.withdrawETH(aavePool, interestAmount, address(this));

        address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        (bool sent,) = payable(treasury).call{ value: interestAmount }("");
        if (!sent) revert TreasuryTransferFailed();

        emit InterestCollectedToTreasury(interestAmount, treasury);
    }
```
