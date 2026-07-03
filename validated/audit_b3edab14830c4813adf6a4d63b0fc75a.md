### Title
`_withdrawFromAave` Does Not Check Actual Aave Pool Liquidity Before Calling `withdrawETH`, Causing `completeWithdrawal` to Revert for ETH Withdrawers - (File: contracts/LRTWithdrawalManager.sol)

### Summary

`LRTWithdrawalManager._withdrawFromAave` computes the withdrawal amount based on the aWETH accounting balance (`aaveAWETH.balanceOf(address(this))`), but does not verify that the Aave pool holds sufficient underlying WETH liquidity before calling `aaveWETHGateway.withdrawETH`. When the Aave WETH pool is highly utilized, `withdrawETH` reverts, causing every user's `completeWithdrawal(ETH_TOKEN, ...)` call to revert, temporarily freezing all pending ETH withdrawals.

### Finding Description

`_withdrawFromAave` caps `withdrawnAmount` to `min(amount, min(aaveBalance, totalETHDepositedToAave))`, where `aaveBalance` is the aWETH token balance — an accounting figure representing the protocol's claim on the pool, not the pool's actual WETH reserves. [1](#0-0) 

The actual WETH liquidity available for withdrawal from the Aave pool is `IERC20(WETH_ADDRESS).balanceOf(address(aaveAWETH))`. This value is already exposed by the contract's own `getAaveWithdrawableLiquidity()` view function: [2](#0-1) 

However, `_withdrawFromAave` never consults this value. When Aave WETH utilization is high (pool liquidity < `withdrawnAmount`), the `aaveWETHGateway.withdrawETH` call at line 917 reverts: [3](#0-2) 

This revert propagates up through `_processWithdrawalCompletion`: [4](#0-3) 

Which is called directly by the user-facing `completeWithdrawal`: [5](#0-4) 

Because the entire transaction reverts, the state changes (`delete withdrawalRequests[requestId]`, `popFront()`, `unlockedWithdrawalsCount[asset]--`) are also reverted, so the user's request is not lost. However, the user is unable to complete their withdrawal for as long as Aave pool liquidity remains insufficient.

### Impact Explanation

**Medium — Temporary freezing of funds.**

All users with unlocked ETH withdrawal requests are blocked from calling `completeWithdrawal(ETH_TOKEN, ...)` for the duration of the Aave pool liquidity shortage. The rsETH they burned at `initiateWithdrawal` time is held in the contract, and the ETH they are owed cannot be claimed. No funds are permanently lost (the request survives the revert), but access to owed ETH is frozen until Aave utilization drops.

### Likelihood Explanation

**Medium.** The Aave WETH market on Ethereum Mainnet regularly experiences utilization spikes above 95% during periods of market stress (e.g., ETH price volatility, liquidation cascades). The Aave integration is a live, enabled feature of `LRTWithdrawalManager`. Any user whose ETH withdrawal is unlocked and whose `expectedAssetAmount` exceeds the contract's idle ETH balance will trigger this path on every `completeWithdrawal` attempt. No attacker action is required; normal market conditions are sufficient.

### Recommendation

Before calling `aaveWETHGateway.withdrawETH`, cap `withdrawnAmount` to the actual WETH liquidity available in the Aave pool. The contract already has the necessary view:

```solidity
function _withdrawFromAave(uint256 amount) internal returns (uint256 withdrawnAmount) {
    if (amount == 0) return 0;

    uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
    if (aaveBalance == 0) revert InsufficientAaveBalance();

    uint256 withdrawablePrincipal = aaveBalance < totalETHDepositedToAave
        ? aaveBalance
        : totalETHDepositedToAave;

+   // Cap to actual WETH liquidity in the Aave pool to avoid revert on high utilization
+   uint256 poolLiquidity = IERC20(WETH_ADDRESS).balanceOf(address(aaveAWETH));
+   if (withdrawablePrincipal > poolLiquidity) withdrawablePrincipal = poolLiquidity;

    withdrawnAmount = amount > withdrawablePrincipal ? withdrawablePrincipal : amount;
    if (withdrawnAmount == 0) return 0;

    aaveWETHGateway.withdrawETH(aavePool, withdrawnAmount, address(this));
    totalETHDepositedToAave -= withdrawnAmount;
    emit ETHWithdrawnFromAave(withdrawnAmount, totalETHDepositedToAave);
}
```

Then in `_processWithdrawalCompletion`, the existing post-withdrawal balance check (`if (balanceAfter < request.expectedAssetAmount) revert InsufficientLiquidityForWithdrawal()`) will correctly surface the shortfall without causing an unexpected external revert. [1](#0-0) 

### Proof of Concept

1. Manager enables Aave integration (`setAaveIntegrationEnabled(true)`).
2. Operator calls `unlockQueue(ETH_TOKEN, ...)`. The contract pulls ETH from `LRTUnstakingVault` and deposits it to Aave via `_depositToAave`. `totalETHDepositedToAave = X`.
3. Aave WETH pool utilization spikes to ~100%; available WETH liquidity drops to `L < X`.
4. User calls `completeWithdrawal(ETH_TOKEN, referralId)`.
5. `_processWithdrawalCompletion` finds `address(this).balance < request.expectedAssetAmount`, computes `amountNeeded`, and calls `_withdrawFromAave(amountNeeded)`.
6. `_withdrawFromAave` computes `withdrawnAmount = min(amountNeeded, X)` (bounded by aWETH accounting balance, not pool liquidity).
7. `aaveWETHGateway.withdrawETH(aavePool, withdrawnAmount, address(this))` is called with `withdrawnAmount > L`.
8. Aave pool reverts due to insufficient liquidity.
9. The entire `completeWithdrawal` transaction reverts.
10. All ETH withdrawers are blocked until Aave utilization recovers. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L183-185)
```text
    function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
        _processWithdrawalCompletion(asset, msg.sender, referralId);
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
