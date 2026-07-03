### Title
Aave WETH Liquidity Drain Causes Temporary Freezing of ETH Withdrawal Funds — (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

When `isAaveIntegrationEnabled == true` and the Aave v3 WETH pool's underlying liquidity is fully borrowed out by external actors, any user with an unlocked ETH withdrawal request will be unable to call `completeWithdrawal(ETH_TOKEN, ...)`. The call reverts because `aaveWETHGateway.withdrawETH` propagates an Aave-level revert before the protocol's own `InsufficientLiquidityForWithdrawal` guard can fire. The user's funds are not lost (the revert rolls back all state), but they are temporarily frozen until Aave liquidity is restored.

---

### Finding Description

`_processWithdrawalCompletion` in `LRTWithdrawalManager.sol` contains the following ETH/Aave path:

```
// lines 720-731
if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN) {
    uint256 contractBalance = address(this).balance;
    if (contractBalance < request.expectedAssetAmount) {
        uint256 amountNeeded = request.expectedAssetAmount - contractBalance;
        _withdrawFromAave(amountNeeded);          // <-- can revert from Aave

        uint256 balanceAfter = address(this).balance;
        if (balanceAfter < request.expectedAssetAmount) {
            revert InsufficientLiquidityForWithdrawal();  // never reached
        }
    }
}
``` [1](#0-0) 

`_withdrawFromAave` checks only the **aToken accounting balance** (`aaveAWETH.balanceOf(address(this))`), not the **underlying WETH liquidity** (`IERC20(WETH_ADDRESS).balanceOf(address(aaveAWETH))`):

```
// lines 908-917
uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
if (aaveBalance == 0) revert InsufficientAaveBalance();
...
aaveWETHGateway.withdrawETH(aavePool, withdrawnAmount, address(this));
``` [2](#0-1) 

When external borrowers have borrowed all WETH from the Aave pool, `aaveAWETH.balanceOf(address(this))` is still positive (aTokens accrue interest regardless of borrow utilization), but `aaveWETHGateway.withdrawETH` will revert at the Aave pool level because there is no underlying WETH to transfer. This revert propagates uncaught through `_withdrawFromAave` → `_processWithdrawalCompletion` → `completeWithdrawal`, blocking the user.

The protocol already exposes a view function that measures the exact gap:

```
// lines 684-688
function getAaveWithdrawableLiquidity() external view returns (uint256 withdrawableLiquidity) {
    if (address(aaveAWETH) == address(0)) return 0;
    return IERC20(WETH_ADDRESS).balanceOf(address(aaveAWETH));
}
``` [3](#0-2) 

This value is never consulted before calling `withdrawETH`. The `depositToAaveExternal` path uses `try/catch` for resilience, but the withdrawal path does not. [4](#0-3) 

---

### Impact Explanation

- **Temporary freezing of user ETH withdrawal funds.** All unlocked ETH withdrawal requests become uncompleable for as long as Aave WETH utilization is 100%.
- Funds are not permanently lost (the revert rolls back `popFront`, `delete withdrawalRequests[requestId]`, and `unlockedWithdrawalsCount--`), but users cannot access their ETH until either (a) Aave liquidity is replenished by repayments, or (b) an operator/pauser calls `emergencyWithdrawFromAave` (requires `PAUSER_ROLE`, not self-serviceable by users).
- Matches the allowed scope: **Medium — Temporary freezing of funds**.

---

### Likelihood Explanation

- Aave v3 WETH on mainnet regularly reaches high utilization (>90%) during market stress events.
- 100% utilization (zero withdrawable liquidity) is a known, documented Aave state that has occurred historically.
- No attacker action is required; normal market borrowing activity is sufficient to trigger this.
- The protocol's own `getAaveWithdrawableLiquidity()` view confirms the gap is observable on-chain.

---

### Recommendation

Before calling `aaveWETHGateway.withdrawETH`, check whether sufficient underlying WETH liquidity exists. If not, either revert with the protocol's own `InsufficientLiquidityForWithdrawal` error (preserving the request in the queue) or wrap the call in `try/catch` and fall back gracefully:

```solidity
// In _withdrawFromAave, before line 917:
uint256 availableLiquidity = IERC20(WETH_ADDRESS).balanceOf(address(aaveAWETH));
if (availableLiquidity < withdrawnAmount) revert InsufficientLiquidityForWithdrawal();
```

Alternatively, wrap the `aaveWETHGateway.withdrawETH` call in a `try/catch` (mirroring the pattern already used in `depositToAaveExternal`) and surface a clear, user-facing error rather than propagating an opaque Aave revert.

---

### Proof of Concept

1. Fork mainnet at a recent block.
2. Deploy/use the existing `LRTWithdrawalManager` with `isAaveIntegrationEnabled = true` and ETH deposited to Aave.
3. Simulate 100% WETH borrow utilization: use a whale account to borrow all available WETH from the Aave v3 pool (or `deal` the aWETH contract's WETH balance to 0 in a Foundry fork test).
4. Assert `getAaveWithdrawableLiquidity() == 0` while `aaveAWETH.balanceOf(withdrawalManager) > 0`.
5. Call `completeWithdrawal(ETH_TOKEN, "")` from a user with an unlocked request where `address(withdrawalManager).balance < request.expectedAssetAmount`.
6. Assert the transaction reverts (Aave-level revert, not `InsufficientLiquidityForWithdrawal`).
7. Assert the user's withdrawal request is still intact in the queue (state rolled back).

### Citations

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

**File:** contracts/LRTWithdrawalManager.sol (L684-689)
```text
    function getAaveWithdrawableLiquidity() external view returns (uint256 withdrawableLiquidity) {
        if (address(aaveAWETH) == address(0)) return 0;

        // The withdrawable liquidity is the WETH balance of the aWETH contract
        return IERC20(WETH_ADDRESS).balanceOf(address(aaveAWETH));
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
