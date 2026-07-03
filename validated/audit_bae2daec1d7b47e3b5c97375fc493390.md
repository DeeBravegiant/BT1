### Title
Aave Pool Pause Creates Deadlock: `setAaveIntegrationEnabled(false)` and `emergencyWithdrawFromAave` Both Revert, Freezing User ETH Withdrawals — (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

When the Aave V3 pool is paused, both `setAaveIntegrationEnabled(false)` and `emergencyWithdrawFromAave` unconditionally call `_withdrawFromAave`, which calls `aaveWETHGateway.withdrawETH`. Since Aave's `withdraw` reverts on a paused pool, neither the disable path nor the emergency exit path can execute. Simultaneously, `completeWithdrawal` for ETH users also calls `_withdrawFromAave` when the contract's idle balance is insufficient, causing those calls to revert too. The result is a complete, protocol-level deadlock for the duration of the Aave pause.

---

### Finding Description

**`setAaveIntegrationEnabled(false)` — no bypass on Aave revert:** [1](#0-0) 

When `enabled == false` and `aaveBalance > 0`, the function first calls `_collectInterestToTreasury()` (which itself calls `aaveWETHGateway.withdrawETH` if any interest has accrued), then calls `_withdrawFromAave(aaveBalance)`. Both calls propagate to: [2](#0-1) 

If the Aave pool is paused, `withdrawETH` reverts. There is no `try/catch`, no skip-if-paused guard, and no alternative code path. The entire `setAaveIntegrationEnabled(false)` transaction reverts, leaving `isAaveIntegrationEnabled` stuck at `true`.

**`emergencyWithdrawFromAave` — same code path, same failure:** [3](#0-2) 

The function requires `isAaveIntegrationEnabled == true` (which it is, since the disable path is blocked), then calls `_collectInterestToTreasury()` and `_withdrawFromAave(amount)` — both of which call `aaveWETHGateway.withdrawETH`. The emergency path is therefore equally blocked.

**`completeWithdrawal` / `_processWithdrawalCompletion` — user ETH withdrawals freeze:** [4](#0-3) 

When `isAaveIntegrationEnabled == true` and the contract's idle ETH balance is less than the requested withdrawal amount, `_withdrawFromAave` is called. With Aave paused, this reverts, blocking all ETH withdrawal completions that depend on Aave liquidity.

**Contrast with deposit path — try/catch is used there but not here:** [5](#0-4) 

The protocol already recognized that Aave calls can fail and wrapped `depositToAaveExternal` in a try/catch inside `unlockQueue`. No equivalent protection exists for the withdrawal direction.

---

### Impact Explanation

- **`setAaveIntegrationEnabled(false)`** reverts for the entire duration of the Aave pause — the integration cannot be disabled.
- **`emergencyWithdrawFromAave`** reverts — the designated emergency exit is non-functional.
- **`completeWithdrawal`** for ETH reverts for any user whose withdrawal requires pulling funds from Aave — user ETH is frozen.
- The only recovery path is waiting for Aave to unpause, or a proxy upgrade (which requires governance delay).

Impact: **Medium — Temporary freezing of user ETH withdrawal funds.**

---

### Likelihood Explanation

Aave V3 has a documented guardian/emergency admin role that can pause the pool. Pool-level pauses have occurred historically during exploit responses. The protocol has no control over when or for how long Aave is paused. During any such pause, all three functions fail simultaneously, with no admin-accessible bypass in the current code.

---

### Recommendation

1. Wrap `aaveWETHGateway.withdrawETH` calls in `setAaveIntegrationEnabled(false)` and `emergencyWithdrawFromAave` with `try/catch`. On failure, allow the disable to proceed anyway (setting `isAaveIntegrationEnabled = false` and revoking approvals), leaving the aWETH balance in place to be recovered once Aave unpauses.
2. Add a separate `forceDisableAaveIntegration()` function (callable by `PAUSER_ROLE`) that sets `isAaveIntegrationEnabled = false` and revokes approvals without attempting any withdrawal — mirroring the pattern already used for deposits.
3. Apply the same `try/catch` pattern to `_collectInterestToTreasury()` when called from the disable/emergency paths.

---

### Proof of Concept

```solidity
// Fork test (Foundry, mainnet fork)
// 1. Deploy/configure LRTWithdrawalManager with live Aave V3 addresses
// 2. Deposit ETH to Aave via depositIdleETHToAave (aaveBalance > 0)
// 3. Simulate Aave guardian pausing the pool:
//    vm.prank(AAVE_EMERGENCY_ADMIN);
//    IPool(AAVE_POOL).setPoolPause(true);
// 4. Call setAaveIntegrationEnabled(false) as LRT manager → assert revert
// 5. Call emergencyWithdrawFromAave(type(uint256).max) as PAUSER_ROLE → assert revert
// 6. Queue and unlock an ETH withdrawal request, then call completeWithdrawal
//    with contract idle balance < withdrawal amount → assert revert (InsufficientLiquidityForWithdrawal
//    or propagated Aave revert)
// 7. Confirm no unblocking path exists without Aave unpausing or a proxy upgrade
```

### Citations

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

**File:** contracts/LRTWithdrawalManager.sol (L507-516)
```text
    /// @notice External wrapper for depositing to Aave (used for try/catch in `unlockQueue`)
    /// @param amount Amount of ETH to deposit
    /// @dev Intentionally NOT `nonReentrant`. `unlockQueue()` is `nonReentrant` and calls this via an external
    ///      self-call (`this.depositToAaveExternal`) to enable try/catch. Marking this as `nonReentrant` would
    ///      make that path always revert due to the shared ReentrancyGuard status. Safety is enforced by
    ///     `msg.sender == address(this)` check.
    function depositToAaveExternal(uint256 amount) external {
        if (msg.sender != address(this)) revert UnauthorizedCaller();
        _depositToAave(amount);
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

**File:** contracts/LRTWithdrawalManager.sol (L917-917)
```text
        aaveWETHGateway.withdrawETH(aavePool, withdrawnAmount, address(this));
```
