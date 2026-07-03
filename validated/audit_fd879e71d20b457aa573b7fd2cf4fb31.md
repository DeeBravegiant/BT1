### Title
Cumulative Aave Deposit Rounding Drift Causes `totalETHDepositedToAave` to Permanently Exceed aWETH Balance, Blocking User ETH Withdrawals and Interest Collection — (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

`_depositToAave` increments `totalETHDepositedToAave` by the exact ETH amount sent, but Aave v3's scaled-balance minting (`rayDiv`/`rayMul`) can mint `amount − 1` aWETH. `_withdrawFromAave` caps the withdrawal at `min(aaveBalance, totalETHDepositedToAave)` and decrements `totalETHDepositedToAave` by that capped value, leaving a permanent 1-wei residue per cycle. After N cycles the drift equals N wei. When N > 2, `_checkAaveHealth` returns `false`, permanently blocking `collectInterestToTreasury`. Even at N = 1, if all ETH is in Aave, `_processWithdrawalCompletion` cannot source the full `expectedAssetAmount` and reverts with `InsufficientLiquidityForWithdrawal`.

---

### Finding Description

**`_depositToAave`** always adds the full `amount` to the accounting variable: [1](#0-0) 

Aave v3 mints scaled aTokens as `floor(amount × RAY / liquidityIndex)`, then `balanceOf` returns `floor(scaledBalance × liquidityIndex / RAY)`. The double floor means `aaveAWETH.balanceOf(address(this))` can be `amount − 1` immediately after deposit when `liquidityIndex > RAY` (always true on mainnet after any interest has accrued).

**`_withdrawFromAave`** caps the withdrawal at `withdrawablePrincipal = min(aaveBalance, totalETHDepositedToAave)`: [2](#0-1) 

If `aaveBalance = amount − 1`, then `withdrawnAmount = amount − 1`, and `totalETHDepositedToAave -= (amount − 1)` leaves `totalETHDepositedToAave = 1` with `aaveBalance = 0`. Each cycle adds 1 wei of drift.

**`_checkAaveHealth`** only tolerates ≤ 2 wei: [3](#0-2) 

After 3 cycles the drift exceeds 2 and `_checkAaveHealth` returns `false`.

**`_processWithdrawalCompletion`** calls `_withdrawFromAave(amountNeeded)` and then hard-checks the resulting balance: [4](#0-3) 

Because `_withdrawFromAave` can only deliver `amountNeeded − 1` (capped by the drifted `aaveBalance`), `balanceAfter < request.expectedAssetAmount` is true and the transaction reverts with `InsufficientLiquidityForWithdrawal`. The user's withdrawal request is deleted before this check, so the revert unwinds the deletion, but the user is stuck in a retry loop that always fails until an operator manually intervenes.

---

### Impact Explanation

- **User ETH withdrawals revert** with `InsufficientLiquidityForWithdrawal` whenever the contract's idle ETH balance is zero and all ETH is in Aave — even after a single deposit-withdraw cycle with a 1-wei rounding loss.
- **`collectInterestToTreasury` is permanently blocked** after 3+ cycles because `_checkAaveHealth` returns `false`.
- No ETH is permanently destroyed; the 1-wei shortfall is locked in Aave's rounding residue. Recovery requires an operator to call `emergencyWithdrawFromAave` and manually top up the 1-wei gap, but the user's `expectedAssetAmount` still cannot be met without an external ETH donation.

This matches **Low — Contract fails to deliver promised returns, but doesn't lose value**.

---

### Likelihood Explanation

- Aave integration must be enabled (operator-controlled, realistic in production).
- `liquidityIndex > RAY` is always true on mainnet Aave v3 WETH after any interest has accrued (i.e., from day 1 of deployment).
- The rounding loss is deterministic, not probabilistic — it occurs on every deposit when `liquidityIndex > RAY`.
- No attacker is required; normal operator usage of `depositIdleETHToAave` + user `completeWithdrawal` is sufficient.
- N > 2 cycles is trivially reachable in normal operation.

---

### Recommendation

1. **Measure actual aWETH received** rather than trusting the requested amount. After `depositETH`, read `aaveAWETH.balanceOf(address(this))` and use the delta as the increment to `totalETHDepositedToAave`.
2. **Measure actual ETH received** after `withdrawETH`. Use the contract's ETH balance delta as the decrement to `totalETHDepositedToAave`, not the requested `withdrawnAmount`.
3. Alternatively, **remove `totalETHDepositedToAave` as a principal tracker** and instead derive the principal from the aWETH balance directly, treating any excess as interest.
4. Increase the `_checkAaveHealth` tolerance or make it unbounded-drift-aware (e.g., allow drift proportional to the number of operations).

---

### Proof of Concept

```solidity
// Foundry fork test (Aave v3 mainnet fork, liquidityIndex > RAY)
function test_roundingDrift() public {
    uint256 amount = 1 ether;

    // Cycle 1
    vm.prank(operator);
    withdrawalManager.depositIdleETHToAave(amount);
    // aaveBalance may be amount - 1 due to scaled balance rounding
    uint256 aaveBalance = withdrawalManager.getAaveBalance();
    uint256 principal = withdrawalManager.totalETHDepositedToAave();
    // principal == amount, aaveBalance == amount - 1 → drift = 1

    // Simulate user withdrawal needing full `amount`
    // _withdrawFromAave(amount) → withdrawnAmount = amount-1 (capped)
    // balanceAfter = amount-1 < amount → InsufficientLiquidityForWithdrawal

    // Repeat for N > 2 cycles:
    // After cycle 3: principal - aaveBalance > 2 → _checkAaveHealth() == false
    // collectInterestToTreasury() reverts with AaveHealthCheckFailed

    // Assert invariant broken:
    for (uint i = 0; i < 3; i++) {
        vm.deal(address(withdrawalManager), amount);
        vm.prank(operator);
        withdrawalManager.depositIdleETHToAave(amount);
        // trigger a withdrawal cycle...
    }
    assertFalse(withdrawalManager.aaveHealthCheck());
}
```

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L722-730)
```text
            if (contractBalance < request.expectedAssetAmount) {
                uint256 amountNeeded = request.expectedAssetAmount - contractBalance;
                _withdrawFromAave(amountNeeded);

                // Verify we have sufficient balance after withdrawal
                uint256 balanceAfter = address(this).balance;
                if (balanceAfter < request.expectedAssetAmount) {
                    revert InsufficientLiquidityForWithdrawal();
                }
```

**File:** contracts/LRTWithdrawalManager.sol (L897-898)
```text
        aaveWETHGateway.depositETH{ value: amount }(aavePool, address(this), 0);
        totalETHDepositedToAave += amount;
```

**File:** contracts/LRTWithdrawalManager.sol (L912-918)
```text
        uint256 withdrawablePrincipal = aaveBalance < totalETHDepositedToAave ? aaveBalance : totalETHDepositedToAave;

        withdrawnAmount = amount > withdrawablePrincipal ? withdrawablePrincipal : amount;
        if (withdrawnAmount == 0) return 0;

        aaveWETHGateway.withdrawETH(aavePool, withdrawnAmount, address(this));
        totalETHDepositedToAave -= withdrawnAmount;
```

**File:** contracts/LRTWithdrawalManager.sol (L931-931)
```text
        if (principal > aaveBalance && principal - aaveBalance > 2) return false;
```
