### Title
Aave WETH Liquidity Drain Blocks All ETH Withdrawal Completions - (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

`LRTWithdrawalManager` deposits idle ETH into Aave v3 to earn yield. When users complete ETH withdrawals and the contract's native balance is insufficient, `_processWithdrawalCompletion` unconditionally calls `_withdrawFromAave`, which calls `aaveWETHGateway.withdrawETH`. An attacker who borrows all WETH from the Aave v3 pool causes this call to revert, permanently blocking every pending ETH `completeWithdrawal` call for as long as the borrow position is held. Critically, the admin escape hatches (`setAaveIntegrationEnabled(false)` and `emergencyWithdrawFromAave`) both internally call the same `_withdrawFromAave` path and are therefore equally blocked.

---

### Finding Description

`LRTWithdrawalManager` integrates with Aave v3 to earn yield on idle ETH. When `unlockQueue` is called for ETH, the unlocked ETH is deposited into Aave via `_depositToAave`. [1](#0-0) 

When a user later calls `completeWithdrawal` (or `completeWithdrawalForUser`), `_processWithdrawalCompletion` is invoked. If the contract's native ETH balance is less than the user's `expectedAssetAmount` and `isAaveIntegrationEnabled` is `true`, it calls `_withdrawFromAave`: [2](#0-1) 

`_withdrawFromAave` calls `aaveWETHGateway.withdrawETH`, which internally calls `IPool.withdraw` on the Aave pool: [3](#0-2) 

Aave v3 (like v2) will lend out all of its WETH reserves. If an attacker borrows all available WETH from the pool, `aaveWETHGateway.withdrawETH` reverts because there is no underlying WETH liquidity to return. This revert propagates through `_processWithdrawalCompletion`, causing every ETH `completeWithdrawal` call to revert with `InsufficientLiquidityForWithdrawal`. [4](#0-3) 

**Admin escape hatches are also blocked.** `setAaveIntegrationEnabled(false)` attempts to withdraw all Aave funds before disabling: [5](#0-4) 

`emergencyWithdrawFromAave` (callable by `PAUSER_ROLE`) also calls `_withdrawFromAave`: [6](#0-5) 

Both paths call `aaveWETHGateway.withdrawETH` and will revert identically. The protocol has no on-chain path to unblock withdrawals while the attacker holds the borrow position.

---

### Impact Explanation

All ETH `completeWithdrawal` and `completeWithdrawalForUser` calls revert for every user who has an unlocked ETH withdrawal request, for as long as the attacker maintains their WETH borrow. Users have already burned rsETH (at `initiateWithdrawal` time) and cannot recover it. This constitutes a **temporary freezing of funds** (Medium severity per the allowed impact scope). If the attacker combines this with a short position on rsETH, the disruption can be made profitable, extending the economic incentive to maintain the attack. [7](#0-6) 

---

### Likelihood Explanation

The attack requires no front-running. An attacker with sufficient collateral (any Aave-accepted asset, e.g., USDC, stETH) can borrow all WETH from Aave v3 at any time. The cost is the borrow interest rate, which at even 100% APR is only ~0.27% per day — a low cost relative to the disruption caused. The attack is unconditional once the Aave integration is enabled and ETH has been deposited into Aave. [8](#0-7) 

---

### Recommendation

1. **Decouple withdrawal completion from Aave liquidity.** In `_processWithdrawalCompletion`, wrap the `_withdrawFromAave` call in a `try/catch` or check available Aave liquidity before calling. If Aave cannot supply the needed ETH, fall back gracefully (e.g., revert with a clear message that does not permanently block the request, or allow partial completion from contract balance).

2. **Add a bypass for admin disabling.** `setAaveIntegrationEnabled(false)` should not call `_withdrawFromAave` if the pool is illiquid. Instead, it should set `isAaveIntegrationEnabled = false` immediately and allow a separate, retryable `emergencyWithdrawFromAave` call once liquidity returns.

3. **Track Aave liquidity before depositing.** Use `getAaveWithdrawableLiquidity()` (already present) to gate deposits, and consider keeping a minimum ETH buffer in the contract to service withdrawals without relying on Aave. [9](#0-8) 

---

### Proof of Concept

```
Setup:
- LRTWithdrawalManager has isAaveIntegrationEnabled = true
- 100 ETH has been deposited to Aave via unlockQueue → _depositToAave
- Alice has an unlocked ETH withdrawal request for 10 ETH

Attack:
1. Attacker deposits 10,000 USDC as collateral into Aave v3
2. Attacker calls aavePool.borrow(WETH, totalWETHInPool, 2, 0, attacker)
   → All WETH is now borrowed; aWETH contract holds 0 WETH

3. Alice calls LRTWithdrawalManager.completeWithdrawal(ETH_TOKEN, referralId)
   → _processWithdrawalCompletion is called
   → address(this).balance < 10 ETH (all ETH was deposited to Aave)
   → _withdrawFromAave(10 ETH) is called
   → aaveWETHGateway.withdrawETH(aavePool, 10 ETH, address(this)) REVERTS
      (no WETH liquidity in pool)
   → Alice's completeWithdrawal reverts

4. Manager tries setAaveIntegrationEnabled(false)
   → calls _withdrawFromAave(aaveBalance) → REVERTS (same reason)
   → Integration cannot be disabled

5. Pauser tries emergencyWithdrawFromAave(type(uint256).max)
   → calls _withdrawFromAave(amount) → REVERTS (same reason)

Result: All ETH withdrawal completions are frozen until attacker repays the WETH loan.
``` [10](#0-9) [11](#0-10)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L60-66)
```text
    IWrappedTokenGatewayV3 public aaveWETHGateway;
    IAToken public aaveAWETH;
    address public aavePool;
    IPoolDataProvider public aaveDataProvider;
    bool public isAaveIntegrationEnabled;
    uint256 public totalETHDepositedToAave;
    address public constant WETH_ADDRESS = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2;
```

**File:** contracts/LRTWithdrawalManager.sol (L183-185)
```text
    function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
        _processWithdrawalCompletion(asset, msg.sender, referralId);
    }
```

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

**File:** contracts/LRTWithdrawalManager.sol (L551-562)
```text
    function emergencyWithdrawFromAave(uint256 amount) external nonReentrant onlyRole(LRTConstants.PAUSER_ROLE) {
        if (!isAaveIntegrationEnabled) revert AaveIntegrationNotEnabled();

        uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
        if (aaveBalance == 0) revert InsufficientAaveBalance();

        // First collect any accrued interest to treasury
        _collectInterestToTreasury();

        uint256 withdrawnAmount = _withdrawFromAave(amount);

        emit EmergencyWithdrawFromAave(withdrawnAmount, address(this));
```

**File:** contracts/LRTWithdrawalManager.sol (L681-689)
```text
    /// @notice Get withdrawable liquidity available in Aave
    /// @return withdrawableLiquidity The amount of WETH that can be withdrawn from Aave
    /// @dev This is the underlying WETH balance in the aWETH contract
    function getAaveWithdrawableLiquidity() external view returns (uint256 withdrawableLiquidity) {
        if (address(aaveAWETH) == address(0)) return 0;

        // The withdrawable liquidity is the WETH balance of the aWETH contract
        return IERC20(WETH_ADDRESS).balanceOf(address(aaveAWETH));
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L699-738)
```text
    function _processWithdrawalCompletion(address asset, address user, string calldata referralId) internal {
        if (userAssociatedNonces[asset][user].empty()) {
            revert NoWithdrawalRequests(user, asset);
        }

        // Retrieve and remove the oldest withdrawal request for the user.
        uint256 usersFirstWithdrawalRequestNonce = userAssociatedNonces[asset][user].popFront();
        // Ensure the request is already unlocked.
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();

        bytes32 requestId = getRequestId(asset, usersFirstWithdrawalRequestNonce);
        WithdrawalRequest memory request = withdrawalRequests[requestId];

        delete withdrawalRequests[requestId];

        // Check that the withdrawal delay has passed since the request's initiation.
        if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();

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
        }

        _transferAsset(asset, user, request.expectedAssetAmount);

        emit ReferralIdEmitted(referralId);
        emit AssetWithdrawalFinalized(user, asset, request.rsETHUnstaked, request.expectedAssetAmount);
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
