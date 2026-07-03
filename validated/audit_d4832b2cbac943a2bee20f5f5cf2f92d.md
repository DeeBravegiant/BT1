### Title
Aave 100% WETH Utilization Permanently Blocks ETH Withdrawal Completions With No Functional Escape Hatch — (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

When `isAaveIntegrationEnabled` is `true` and the contract's native ETH balance is insufficient to cover a pending withdrawal, `_processWithdrawalCompletion` calls `_withdrawFromAave` with no try/catch. If Aave's WETH pool has 100% utilization, `aaveWETHGateway.withdrawETH` reverts, propagating the revert all the way up and blocking every ETH withdrawal completion. Critically, every intended escape hatch — `emergencyWithdrawFromAave`, `setAaveIntegrationEnabled(false)`, and `configureAaveIntegration` — also calls `_withdrawFromAave` unconditionally, so they all revert under the same condition. There is no code path that can unblock withdrawals while Aave remains illiquid.

---

### Finding Description

**Entrypoint:** `completeWithdrawal(ETH_TOKEN, referralId)` → `_processWithdrawalCompletion`

**Execution path:**

```
completeWithdrawal
  └─ _processWithdrawalCompletion (line 699)
       └─ isAaveIntegrationEnabled && asset == ETH_TOKEN (line 720)
            └─ contractBalance < request.expectedAssetAmount (line 722)
                 └─ _withdrawFromAave(amountNeeded) (line 724)  ← no try/catch
                      └─ aaveWETHGateway.withdrawETH(...) (line 917) ← REVERTS
``` [1](#0-0) 

`_withdrawFromAave` makes a bare external call to `aaveWETHGateway.withdrawETH` with no error handling: [2](#0-1) 

When Aave's WETH reserve is fully utilized, `withdrawETH` reverts (Aave v3 error `26` — `UNDERLYING_BALANCE_ZERO`). This revert propagates uncaught through `_processWithdrawalCompletion`, making every ETH `completeWithdrawal` call revert.

**All escape hatches are equally broken:**

| Function | Why it also reverts |
|---|---|
| `emergencyWithdrawFromAave` | Calls `_withdrawFromAave(amount)` at line 560 — same revert path |
| `setAaveIntegrationEnabled(false)` | Calls `_withdrawFromAave(aaveBalance)` at line 495 — same revert path |
| `configureAaveIntegration(...)` | Calls `_withdrawFromAave(aaveBalance)` at line 447 — same revert path | [3](#0-2) [4](#0-3) 

The question's claim that `emergencyWithdrawFromAave` unblocks the situation is **incorrect** — it calls the same `_withdrawFromAave` → `aaveWETHGateway.withdrawETH` path and reverts identically. The only resolution is waiting for external Aave liquidity to be restored by third-party borrowers repaying loans.

---

### Impact Explanation

**Temporary freezing of funds (Medium).** All ETH withdrawal completions are blocked for every user with an unlocked request. The funds are not lost — they remain in Aave as aWETH — but users cannot access them until Aave WETH utilization drops below 100%. There is no operator action that can unblock the queue while Aave remains illiquid, because every administrative function that touches Aave also calls `_withdrawFromAave` unconditionally.

---

### Likelihood Explanation

Aave v3 WETH utilization reaching 100% is a realistic market condition during periods of high borrowing demand (e.g., ETH price volatility, liquidation cascades, or coordinated borrowing). It does not require any attacker action — it is a normal market state. The preconditions (Aave integration enabled, ETH deposited to Aave, contract balance below withdrawal amount) are the intended steady-state operating conditions of the protocol.

---

### Recommendation

1. **Wrap `_withdrawFromAave` in `_processWithdrawalCompletion` with try/catch.** If the Aave withdrawal fails, fall back to reverting with a specific `AaveWithdrawalFailed` error that does not consume the user's nonce (restore `userAssociatedNonces` before reverting), so the user can retry later.

2. **Add a force-disable path for `setAaveIntegrationEnabled(false)`.** Allow disabling Aave integration without withdrawing funds (e.g., a `forceDisable` flag), so the manager can stop routing new withdrawals through Aave even when Aave is illiquid.

3. **Fix `emergencyWithdrawFromAave`** to use try/catch and emit a failure event rather than reverting, so the PAUSER_ROLE can at least record the state and take other actions.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.21;

import "forge-std/Test.sol";
import {LRTWithdrawalManager} from "contracts/LRTWithdrawalManager.sol";

// Fork test: mainnet block where Aave v3 WETH utilization ~100%
contract AaveUtilizationFreezeTest is Test {
    uint256 mainnetFork;
    LRTWithdrawalManager withdrawalManager;

    function setUp() public {
        // Fork at a block with high WETH utilization
        mainnetFork = vm.createFork(vm.envString("MAINNET_RPC_URL"), HIGH_UTIL_BLOCK);
        vm.selectFork(mainnetFork);

        // Deploy/configure protocol with Aave integration enabled
        // ... (standard test setup) ...
    }

    function test_aaveUtilizationFreezesWithdrawals() public {
        // 1. Deposit ETH, unlock a withdrawal request
        // 2. Deposit all contract ETH to Aave (so contractBalance == 0)
        // 3. Simulate Aave WETH utilization at 100% by draining WETH from aWETH contract
        deal(WETH_ADDRESS, address(aaveAWETH), 0);

        // 4. Assert completeWithdrawal reverts
        vm.expectRevert(); // Aave: UNDERLYING_BALANCE_ZERO
        withdrawalManager.completeWithdrawal(ETH_TOKEN, "ref");

        // 5. Assert emergencyWithdrawFromAave ALSO reverts (escape hatch is broken)
        vm.prank(pauserRole);
        vm.expectRevert(); // same revert path
        withdrawalManager.emergencyWithdrawFromAave(type(uint256).max);

        // 6. Assert setAaveIntegrationEnabled(false) ALSO reverts
        vm.prank(managerRole);
        vm.expectRevert();
        withdrawalManager.setAaveIntegrationEnabled(false);
    }
}
```

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
