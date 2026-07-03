### Title
Aave WETH Pool High Utilization Permanently Blocks ETH `completeWithdrawal` With No Admin Escape Hatch — (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

When `isAaveIntegrationEnabled` is true and the Aave v3 WETH pool reaches high utilization (available WETH liquidity < `request.expectedAssetAmount`), the call to `aaveWETHGateway.withdrawETH` inside `_withdrawFromAave` reverts. Because there is no `try/catch` around this call in `_processWithdrawalCompletion`, the revert propagates and causes every ETH `completeWithdrawal` call to revert. Critically, all admin escape hatches (`emergencyWithdrawFromAave`, `setAaveIntegrationEnabled(false)`) route through the same `_withdrawFromAave` path and are equally blocked, leaving no on-chain remedy until Aave liquidity recovers.

---

### Finding Description

**Entrypoint**: `completeWithdrawal(ETH_TOKEN, referralId)` → `_processWithdrawalCompletion` → `_withdrawFromAave` → `aaveWETHGateway.withdrawETH`.

In `_processWithdrawalCompletion`, when the contract's native ETH balance is insufficient to cover `request.expectedAssetAmount`, the code calls `_withdrawFromAave(amountNeeded)` with no error handling: [1](#0-0) 

Inside `_withdrawFromAave`, the call to the Aave gateway is bare — no `try/catch`, no liquidity pre-check: [2](#0-1) 

Aave v3's `Pool.withdraw` reverts with `UNDERLYING_CLAIMABLE_RIGHTS_NOT_ENOUGH` (or equivalent) when `IERC20(WETH).balanceOf(aWETH) < amount`. This revert propagates atomically through `_withdrawFromAave` → `_processWithdrawalCompletion` → `completeWithdrawal`, blocking every ETH withdrawal request that requires Aave funds.

**All admin escape hatches are equally blocked:**

- `emergencyWithdrawFromAave` calls `_withdrawFromAave` directly: [3](#0-2) 

- `setAaveIntegrationEnabled(false)` calls `_withdrawFromAave` before clearing the flag: [4](#0-3) 

Because `isAaveIntegrationEnabled` is only set to `false` **after** the withdrawal succeeds (line 503), a revert inside `_withdrawFromAave` prevents the flag from ever being cleared, leaving the integration permanently enabled and all ETH withdrawals permanently blocked until Aave liquidity recovers externally.

**Asymmetric handling**: The protocol correctly uses `try/catch` for Aave deposits via `depositToAaveExternal` to avoid blocking `unlockQueue`: [5](#0-4) 

No equivalent defensive pattern exists for the withdrawal path.

---

### Impact Explanation

All queued, unlocked ETH withdrawal requests — which the protocol has already committed to fulfilling — become uncompletable for the duration of Aave WETH pool illiquidity. Users' rsETH has already been burned at unlock time; they hold a claim on ETH they cannot receive. This is **temporary freezing of funds** (Medium).

---

### Likelihood Explanation

Aave v3 WETH pool utilization has historically exceeded 90% during periods of high WETH borrow demand (e.g., leveraged staking unwinds, rate arbitrage). At 100% utilization, zero liquidity is withdrawable. This is a realistic, market-driven condition requiring no attacker — though a well-capitalized actor could also borrow WETH to push utilization to 100% and grief all ETH withdrawers simultaneously.

---

### Recommendation

Wrap the `_withdrawFromAave` call inside `_processWithdrawalCompletion` in a `try/catch` (mirroring the deposit pattern). On failure, fall back to serving the withdrawal from the contract's native ETH balance if sufficient, or revert with a clear `InsufficientLiquidityForWithdrawal` error that preserves all state (the current revert already preserves state atomically, but the error message is Aave's internal error, not the protocol's). Additionally, decouple `setAaveIntegrationEnabled(false)` from `_withdrawFromAave` so the integration can be disabled even when Aave is illiquid, allowing withdrawals to proceed from native ETH balance alone.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Foundry fork test — run against mainnet fork
// forge test --fork-url $MAINNET_RPC --match-test test_aaveHighUtilizationBlocksWithdrawal -vvvv

import "forge-std/Test.sol";

interface ILRTWithdrawalManager {
    function completeWithdrawal(address asset, string calldata referralId) external;
    function isAaveIntegrationEnabled() external view returns (bool);
    function getAaveWithdrawableLiquidity() external view returns (uint256);
}

contract AaveUtilizationPoC is Test {
    address constant WITHDRAWAL_MANAGER = /* deployed address */;
    address constant WETH = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2;
    address constant AWETH = 0x4d5F47FA6A74757f35C14fD3a6Ef8E3C9BC514E8; // Aave v3 aWETH

    function test_aaveHighUtilizationBlocksWithdrawal() external {
        ILRTWithdrawalManager wm = ILRTWithdrawalManager(WITHDRAWAL_MANAGER);
        assertTrue(wm.isAaveIntegrationEnabled());

        // Drain WETH liquidity from aWETH contract to simulate 100% utilization
        uint256 wethInPool = IERC20(WETH).balanceOf(AWETH);
        vm.prank(AWETH); // impersonate aWETH to drain its own WETH
        IERC20(WETH).transfer(address(0xdead), wethInPool);

        assertEq(wm.getAaveWithdrawableLiquidity(), 0);

        // User with an unlocked ETH withdrawal request calls completeWithdrawal
        address user = /* user with unlocked ETH request */;
        vm.prank(user);
        vm.expectRevert(); // Aave's liquidity error propagates
        wm.completeWithdrawal(0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE, "");
    }
}
```

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L494-496)
```text
                if (aaveBalance > 0) {
                    _withdrawFromAave(aaveBalance);
                }
```

**File:** contracts/LRTWithdrawalManager.sol (L507-515)
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
```

**File:** contracts/LRTWithdrawalManager.sol (L560-560)
```text
        uint256 withdrawnAmount = _withdrawFromAave(amount);
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
