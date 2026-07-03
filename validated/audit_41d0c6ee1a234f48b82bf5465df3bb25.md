### Title
Silent Aave Deposit Failure Leaves ETH Idle and Not Earning Yield - (`contracts/LRTWithdrawalManager.sol`)

### Summary
When `unlockQueue` is called for ETH and the Aave deposit fails (e.g., supply cap hit, pool paused), the try/catch silently swallows the revert, emits `AaveDepositFailed`, and leaves the ETH sitting idle in the contract. The ETH earns no yield until an operator manually calls `depositIdleETHToAave`. If the operator never calls it, the ETH remains permanently idle, violating the protocol's promise to earn Aave yield on queued ETH.

### Finding Description

In `unlockQueue`, after redeeming ETH from the unstaking vault, the contract attempts to deposit it to Aave via a self-call: [1](#0-0) 

If `depositToAaveExternal` reverts (e.g., Aave supply cap reached), the catch block emits `AaveDepositFailed` and execution continues. The ETH remains in `address(this).balance`. Since `_depositToAave` was never reached, `totalETHDepositedToAave` is not incremented — which is technically correct accounting, but the ETH is now idle and earning nothing. [2](#0-1) 

The only recovery path is the operator manually calling `depositIdleETHToAave`: [3](#0-2) 

There is no on-chain enforcement, timeout, or automatic retry that guarantees this call is ever made.

**Correction to the question's `InsufficientLiquidityForWithdrawal` claim:**

The question's secondary claim — that stale `totalETHDepositedToAave` causes `_withdrawFromAave` to cap withdrawals incorrectly, leading to `InsufficientLiquidityForWithdrawal` — is **incorrect**. When the Aave deposit fails, the ETH remains in `address(this).balance`. In `_processWithdrawalCompletion`, the contract checks `contractBalance < request.expectedAssetAmount` first: [4](#0-3) 

Since the ETH is in the contract (not in Aave), `contractBalance >= request.expectedAssetAmount` and the Aave withdrawal branch is never entered. Withdrawals succeed normally. `totalETHDepositedToAave` is also not stale — it correctly reflects zero new deposits since none succeeded.

The `_withdrawFromAave` principal cap: [5](#0-4) 

...is only relevant when ETH is actually in Aave. In the failed-deposit scenario, `aaveBalance` and `totalETHDepositedToAave` are both unchanged, so no mismatch exists.

### Impact Explanation

**Low. Contract fails to deliver promised returns, but doesn't lose value.**

ETH that should be earning Aave yield sits idle in the contract. No funds are lost — users can still complete withdrawals because the ETH is in the contract balance. But the protocol fails to deliver the yield it is designed to generate on queued ETH.

### Likelihood Explanation

Aave supply caps being hit is a realistic, documented condition on mainnet (WETH supply cap has been reached historically). The try/catch is explicitly designed for this scenario. The operator recovery path (`depositIdleETHToAave`) requires off-chain monitoring and manual intervention with no on-chain guarantee of timeliness.

### Recommendation

1. Emit a more prominent alert (e.g., a dedicated monitoring hook or a stricter event) when `AaveDepositFailed` fires, so operators are reliably notified.
2. Consider adding an on-chain tracking variable (e.g., `idleETHPendingAaveDeposit`) that accumulates failed deposit amounts, making the idle balance auditable and distinguishable from ETH held for pending withdrawals.
3. Optionally, implement an automatic retry mechanism or a keeper-compatible function that can be called permissionlessly to deposit idle ETH once Aave capacity is restored.

### Proof of Concept

```
1. Aave WETH supply cap is at maximum.
2. Operator calls unlockQueue(ETH_TOKEN, ...) → assetAmountUnlocked = 100 ETH.
3. unstakingVault.redeem(ETH, 100e18) → 100 ETH transferred to WithdrawalManager.
4. this.depositToAaveExternal(100e18) reverts (supply cap exceeded).
5. Catch block fires: emit AaveDepositFailed(100e18, reason).
6. totalETHDepositedToAave unchanged. address(this).balance += 100e18.
7. Operator never calls depositIdleETHToAave.
8. 100 ETH sits idle indefinitely, earning 0 yield.
9. Users complete withdrawals successfully (ETH is in contract), but the protocol
   earns no Aave interest on this ETH for the entire idle period.
Assert: address(this).balance > 0 && totalETHDepositedToAave unchanged after failed deposit.
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

**File:** contracts/LRTWithdrawalManager.sol (L521-535)
```text
    function depositIdleETHToAave(uint256 amount) external nonReentrant whenNotPaused onlyLRTOperator {
        if (!isAaveIntegrationEnabled) revert AaveIntegrationNotEnabled();

        uint256 idleBalance = address(this).balance;
        if (idleBalance == 0) revert AmountMustBeGreaterThanZero();

        uint256 depositAmount = amount;
        if (amount == type(uint256).max || amount > idleBalance) {
            depositAmount = idleBalance;
        }

        if (depositAmount > 0) {
            _depositToAave(depositAmount);
        }
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L720-731)
```text
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

**File:** contracts/LRTWithdrawalManager.sol (L894-901)
```text
    function _depositToAave(uint256 amount) internal {
        if (amount == 0) return;

        aaveWETHGateway.depositETH{ value: amount }(aavePool, address(this), 0);
        totalETHDepositedToAave += amount;

        emit ETHDepositedToAave(amount, totalETHDepositedToAave);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L911-915)
```text
        // Only withdraw up to the principal amount (don't use accrued interest for user withdrawals)
        uint256 withdrawablePrincipal = aaveBalance < totalETHDepositedToAave ? aaveBalance : totalETHDepositedToAave;

        withdrawnAmount = amount > withdrawablePrincipal ? withdrawablePrincipal : amount;
        if (withdrawnAmount == 0) return 0;
```
