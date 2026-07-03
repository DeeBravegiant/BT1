Audit Report

## Title
`_withdrawFromAave` Silently Returns Zero When `totalETHDepositedToAave == 0`, Permanently Freezing Unlocked ETH Withdrawals — (`contracts/LRTWithdrawalManager.sol`)

## Summary

`_withdrawFromAave` caps withdrawable amounts at `totalETHDepositedToAave`. When that counter reaches zero through normal principal-withdrawal operations while Aave still holds accrued interest (`aaveAWETH.balanceOf > 0`), the function silently returns `0`. `_processWithdrawalCompletion` discards the return value, detects the unchanged balance, and reverts with `InsufficientLiquidityForWithdrawal`, permanently blocking all subsequent unlocked ETH withdrawal requests. No admin rescue path can redirect the stranded interest to users — it can only be swept to the treasury.

## Finding Description

**Root cause — `_withdrawFromAave` (lines 905–921):** [1](#0-0) 

When `totalETHDepositedToAave == 0`:
- `withdrawablePrincipal = min(aaveBalance, 0) = 0`
- `withdrawnAmount = 0`
- The function returns `0` silently without touching Aave or reverting.

**Caller discards the return value — `_processWithdrawalCompletion` (lines 719–731):** [2](#0-1) 

The return value of `_withdrawFromAave` on line 724 is discarded. Because nothing was withdrawn, `balanceAfter == contractBalance < request.expectedAssetAmount`, and the revert on line 729 fires unconditionally.

**How `totalETHDepositedToAave == 0, aaveBalance > 0` is reached in normal operation:**

1. 100 ETH deposited → `totalETHDepositedToAave = 100`, `aaveBalance ≈ 100`.
2. Interest accrues → `aaveBalance = 100 + ε`.
3. A withdrawal completion needs exactly 100 ETH (contract balance = 0):
   - `withdrawablePrincipal = min(100+ε, 100) = 100`
   - `withdrawnAmount = 100`, `totalETHDepositedToAave → 0`
   - `aaveBalance → ε` (interest remains in Aave)
4. Any subsequent unlocked withdrawal request (even for 1 wei) hits the frozen state.

**No rescue path exists for users:**

`_collectInterestToTreasury` (lines 945–961) computes `interestAmount = aaveBalance - principal`. When `principal == 0`, this withdraws the entire remaining `aaveBalance` and sends it to the **treasury**, not to users. [3](#0-2) 

Any admin function (`emergencyWithdrawFromAave`, `setAaveIntegrationEnabled(false)`, `configureAaveIntegration`) that calls `_collectInterestToTreasury` first routes the interest to the treasury, after which `aaveBalance == 0` and `_withdrawFromAave` reverts with `InsufficientAaveBalance`. Users remain permanently frozen.

## Impact Explanation

**Critical — Permanent freezing of funds.** Any user with an already-unlocked ETH withdrawal request (past the delay, already committed) cannot complete it once `totalETHDepositedToAave == 0` and `aaveBalance > 0`. The ETH exists in Aave but is permanently inaccessible to users; the only on-chain path for those funds routes them to the treasury. The freeze is irreversible because no admin function can redirect the interest to satisfy user withdrawals.

## Likelihood Explanation

High. Interest accrual on Aave is continuous and automatic. The state `totalETHDepositedToAave == 0, aaveBalance > 0` is reached every time the last unit of principal is withdrawn while any interest has accrued — a routine occurrence in normal operation. No attacker action is required; it happens organically as users complete withdrawals.

## Recommendation

In `_withdrawFromAave`, when `totalETHDepositedToAave == 0` but `aaveBalance > 0`, allow withdrawing up to the full `aaveBalance` (treating residual interest as available liquidity) and set `totalETHDepositedToAave` to `0` after the withdrawal:

```solidity
uint256 withdrawablePrincipal = aaveBalance < totalETHDepositedToAave
    ? aaveBalance
    : totalETHDepositedToAave;

// If principal is exhausted but interest remains, allow using it
if (withdrawablePrincipal == 0 && aaveBalance > 0) {
    withdrawablePrincipal = aaveBalance;
}
```

Additionally, `_processWithdrawalCompletion` should check the return value of `_withdrawFromAave` and revert with a descriptive error if `withdrawnAmount < amountNeeded`, rather than relying solely on the post-balance check. [4](#0-3) 

## Proof of Concept

```solidity
function test_frozenWithdrawal_whenPrincipalZeroInterestNonZero() public {
    // 1. Deposit 1 ETH to Aave
    vm.deal(address(withdrawalManager), 1 ether);
    vm.prank(operator);
    withdrawalManager.depositIdleETHToAave(1 ether);
    // totalETHDepositedToAave = 1e18, aaveBalance ≈ 1e18

    // 2. Simulate interest accrual: mock aaveAWETH to return 1e18 + 1 wei
    mockAaveAWETH.setBalance(address(withdrawalManager), 1 ether + 1);

    // 3. Complete a withdrawal that drains principal to 0
    //    withdrawablePrincipal = min(1e18+1, 1e18) = 1e18 → totalETHDepositedToAave = 0
    //    aaveBalance = 1 wei remains as interest
    withdrawalManager.completeWithdrawal(ETH_TOKEN, "");

    // 4. Any subsequent unlocked request now permanently reverts:
    //    _withdrawFromAave returns 0 silently → balanceAfter < expectedAssetAmount → revert
    vm.expectRevert(ILRTWithdrawalManager.InsufficientLiquidityForWithdrawal.selector);
    withdrawalManager.completeWithdrawal(ETH_TOKEN, "");
}
``` [2](#0-1) [4](#0-3)

### Citations

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
