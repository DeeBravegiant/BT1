### Title
`setAaveIntegrationEnabled(false)` Can Revert When Aave Is Paused, Permanently Blocking ETH Withdrawal Completion - (File: `contracts/LRTWithdrawalManager.sol`)

---

### Summary

`LRTWithdrawalManager.setAaveIntegrationEnabled(false)` attempts to withdraw all ETH from Aave before setting `isAaveIntegrationEnabled = false`. If Aave is paused, both withdrawal calls revert, making it impossible to disable the integration. Because `completeWithdrawal` for ETH unconditionally calls `_withdrawFromAave` when `isAaveIntegrationEnabled == true`, all pending ETH withdrawals are also frozen for the duration of the Aave pause.

---

### Finding Description

`setAaveIntegrationEnabled` is the administrative function used to disable the Aave yield integration in an emergency. When called with `enabled = false`, it executes two external calls to Aave before updating the flag:

```solidity
// contracts/LRTWithdrawalManager.sol:486-503
if (!enabled) {
    uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
    if (aaveBalance > 0) {
        _collectInterestToTreasury();          // → aaveWETHGateway.withdrawETH(...)
        aaveBalance = aaveAWETH.balanceOf(address(this));
        if (aaveBalance > 0) {
            _withdrawFromAave(aaveBalance);    // → aaveWETHGateway.withdrawETH(...)
        }
    }
    _revokeApprovalToAaveWETHGateway();
}

isAaveIntegrationEnabled = enabled;           // ← state change happens LAST
``` [1](#0-0) 

`_collectInterestToTreasury` calls `aaveWETHGateway.withdrawETH(...)`: [2](#0-1) 

`_withdrawFromAave` also calls `aaveWETHGateway.withdrawETH(...)`: [3](#0-2) 

Aave v3 has a guardian role that can pause individual reserves or the entire protocol. When WETH is paused, `withdrawETH` reverts. Because both external calls precede the `isAaveIntegrationEnabled = false` assignment, the flag can never be cleared while Aave is paused.

The downstream consequence is in `_processWithdrawalCompletion`, which is called by every `completeWithdrawal` and `completeWithdrawalForUser` invocation for ETH:

```solidity
// contracts/LRTWithdrawalManager.sol:720-731
if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN) {
    uint256 contractBalance = address(this).balance;
    if (contractBalance < request.expectedAssetAmount) {
        uint256 amountNeeded = request.expectedAssetAmount - contractBalance;
        _withdrawFromAave(amountNeeded);   // ← reverts if Aave is paused
        ...
    }
}
``` [4](#0-3) 

Because `isAaveIntegrationEnabled` cannot be set to `false` while Aave is paused, and because `_withdrawFromAave` is called unconditionally when the flag is `true` and the contract's ETH balance is insufficient, all ETH withdrawal completions revert for the duration of the Aave pause.

The `emergencyWithdrawFromAave` function (callable by `PAUSER_ROLE`) has the same defect — it also calls `_collectInterestToTreasury()` and `_withdrawFromAave()` before returning, so it too reverts when Aave is paused: [5](#0-4) 

---

### Impact Explanation

**Temporary freezing of funds (Medium).**

All users who have unlocked ETH withdrawal requests cannot call `completeWithdrawal` while Aave is paused, because the function unconditionally attempts to pull ETH from Aave when `isAaveIntegrationEnabled == true`. The manager cannot flip the flag to `false` to bypass Aave, because `setAaveIntegrationEnabled(false)` itself reverts. Funds are not permanently lost — they remain in Aave — but they are inaccessible to users for the entire duration of the Aave pause.

---

### Likelihood Explanation

Aave v3 has a well-documented guardian/emergency admin role that can pause individual asset reserves. WETH is one of the largest Aave v3 markets and has been paused in the past during security incidents. The scenario is realistic and requires no attacker action — a routine Aave guardian pause is sufficient to trigger the freeze.

---

### Recommendation

Separate the state change from the withdrawal. Accept an optional `bool skipWithdraw` parameter (mirroring the M-32 recommendation), or simply set `isAaveIntegrationEnabled = false` first and then attempt the withdrawal in a separate, non-reverting step:

```solidity
function setAaveIntegrationEnabled(bool enabled) external nonReentrant onlyLRTManager {
    if (enabled == isAaveIntegrationEnabled) revert AaveIntegrationAlreadyInDesiredState(enabled);

    isAaveIntegrationEnabled = enabled;   // ← set state first
    emit AaveIntegrationEnabled(enabled);

    if (!enabled) {
        // Best-effort withdrawal; failure does not block disabling
        uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
        if (aaveBalance > 0) {
            try this.withdrawFromAaveExternal(aaveBalance) {} catch {}
        }
        _revokeApprovalToAaveWETHGateway();
    }
    ...
}
```

Alternatively, expose a separate `withdrawFromAave()` function that the manager can call once Aave is unpaused, and document that `setAaveIntegrationEnabled(false)` does not guarantee immediate fund retrieval.

---

### Proof of Concept

1. Protocol has ETH deposited in Aave via `LRTWithdrawalManager` (`isAaveIntegrationEnabled == true`, `totalETHDepositedToAave > 0`).
2. Aave guardian pauses the WETH reserve (e.g., in response to a security incident).
3. Manager calls `setAaveIntegrationEnabled(false)` to stop routing through Aave.
4. Inside the call, `_collectInterestToTreasury()` calls `aaveWETHGateway.withdrawETH(...)` → Aave reverts with `RESERVE_PAUSED`.
5. The entire `setAaveIntegrationEnabled` transaction reverts; `isAaveIntegrationEnabled` remains `true`.
6. Users with unlocked ETH withdrawal requests call `completeWithdrawal(ETH_TOKEN, ...)`.
7. `_processWithdrawalCompletion` reaches line 724 and calls `_withdrawFromAave(amountNeeded)` → Aave reverts again.
8. All ETH withdrawal completions revert. Users cannot retrieve their ETH for the duration of the Aave pause. [6](#0-5) [4](#0-3)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L469-505)
```text
    function setAaveIntegrationEnabled(bool enabled) external nonReentrant onlyLRTManager {
        if (enabled == isAaveIntegrationEnabled) {
            revert AaveIntegrationAlreadyInDesiredState(enabled);
        }

        if (enabled) {
            if (
                address(aaveWETHGateway) == address(0) || address(aaveAWETH) == address(0) || aavePool == address(0)
                    || address(aaveDataProvider) == address(0)
            ) {
                revert InvalidAaveConfiguration();
            }

            // Approve aWETH to WETH Gateway for withdrawals
            IERC20(address(aaveAWETH)).forceApprove(address(aaveWETHGateway), type(uint256).max);
        }

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

        isAaveIntegrationEnabled = enabled;
        emit AaveIntegrationEnabled(enabled);
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

**File:** contracts/LRTWithdrawalManager.sol (L954-958)
```text
        aaveWETHGateway.withdrawETH(aavePool, interestAmount, address(this));

        address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        (bool sent,) = payable(treasury).call{ value: interestAmount }("");
        if (!sent) revert TreasuryTransferFailed();
```
