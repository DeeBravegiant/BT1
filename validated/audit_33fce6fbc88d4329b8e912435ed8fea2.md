Audit Report

## Title
Silent Zero-Return in `_withdrawFromAave` When `totalETHDepositedToAave == 0` With Non-Zero aWETH Balance Causes Temporary Freezing of User ETH Withdrawals — (`contracts/LRTWithdrawalManager.sol`)

## Summary
`_withdrawFromAave` computes `withdrawablePrincipal = min(aaveBalance, totalETHDepositedToAave)`. When `totalETHDepositedToAave == 0` but `aaveAWETH.balanceOf(address(this)) > 0`, the function bypasses the `aaveBalance == 0` guard, computes `withdrawablePrincipal = 0`, and silently returns `0`. The caller `_processWithdrawalCompletion` ignores the return value and reverts with `InsufficientLiquidityForWithdrawal`, blocking all pending ETH user withdrawals until multi-step admin recovery is performed.

## Finding Description
**Root cause — `_withdrawFromAave`** (lines 905–921):

```solidity
uint256 aaveBalance = aaveAWETH.balanceOf(address(this));
if (aaveBalance == 0) revert InsufficientAaveBalance();   // passes when aaveBalance > 0

uint256 withdrawablePrincipal = aaveBalance < totalETHDepositedToAave
    ? aaveBalance
    : totalETHDepositedToAave;                            // = min(aaveBalance, 0) = 0

withdrawnAmount = amount > withdrawablePrincipal ? withdrawablePrincipal : amount;
if (withdrawnAmount == 0) return 0;                       // silent return, no revert
```

When `totalETHDepositedToAave == 0` and `aaveBalance > 0`, `withdrawablePrincipal` evaluates to `0`, `withdrawnAmount` evaluates to `0`, and the function returns silently without withdrawing anything or reverting.

**Caller — `_processWithdrawalCompletion`** (lines 719–731):

```solidity
_withdrawFromAave(amountNeeded);          // returns 0, nothing withdrawn
uint256 balanceAfter = address(this).balance;
if (balanceAfter < request.expectedAssetAmount) {
    revert InsufficientLiquidityForWithdrawal();  // always reverts
}
```

The return value is not checked; the post-withdrawal balance check catches the failure and reverts, blocking the user's withdrawal.

**How `totalETHDepositedToAave == 0` with `aaveBalance > 0` is reached (natural rounding path — no attacker required):**

After a full deposit/withdrawal cycle, Aave's internal accounting may leave 1–2 wei of aWETH in the contract while `totalETHDepositedToAave` reaches exactly `0`. The protocol itself acknowledges this in `_checkAaveHealth` (lines 929–931), which explicitly tolerates up to 2 wei of discrepancy:

```solidity
if (principal > aaveBalance && principal - aaveBalance > 2) return false;
```

However, `_withdrawFromAave` does not apply the same tolerance, leaving the residual 1–2 wei as a trigger for the silent zero-return.

**Recovery path is insufficient:**

`_collectInterestToTreasury` (lines 945–961) treats the entire residual aWETH as "interest" (since `aaveBalance > principal = 0`) and withdraws it — but sends the resulting ETH to the **treasury**, not to the contract. The contract's ETH balance is not restored by this call. The treasury admin must then manually return ETH to the contract before users can complete withdrawals.

## Impact Explanation
All pending ETH withdrawal requests that require Aave liquidity (`contractBalance < request.expectedAssetAmount`) revert with `InsufficientLiquidityForWithdrawal`. Users cannot complete their withdrawals until an operator drains the residual aWETH to treasury via `collectInterestToTreasury` and the treasury admin manually returns ETH to the contract. This constitutes **Medium — Temporary freezing of user ETH withdrawal funds**.

## Likelihood Explanation
The rounding path requires no attacker and can occur naturally after any full deposit/withdrawal cycle through Aave. The `_checkAaveHealth` function's explicit 2-wei tolerance confirms the protocol designers anticipated this rounding behavior, making it a realistic operational condition. The vulnerable state (`totalETHDepositedToAave == 0`, Aave integration enabled, contract ETH balance insufficient for a pending withdrawal) is a realistic operational window, particularly just after Aave integration is first enabled or after a full principal withdrawal cycle. Additionally, a permissionless donation path exists: any address can acquire aWETH on the open market and `transfer` it directly to `LRTWithdrawalManager`, bypassing `_depositToAave` and leaving `totalETHDepositedToAave` at `0` while `aaveBalance > 0`.

## Recommendation
Replace the silent `return 0` with logic that uses the actual `aaveBalance` as the cap when `totalETHDepositedToAave` is zero, consistent with how `_checkAaveHealth` handles the rounding tolerance:

```solidity
uint256 withdrawablePrincipal = totalETHDepositedToAave == 0
    ? aaveBalance
    : (aaveBalance < totalETHDepositedToAave ? aaveBalance : totalETHDepositedToAave);
```

Additionally, add an explicit check in `_processWithdrawalCompletion` to revert immediately if `_withdrawFromAave` returns less than `amountNeeded`, rather than relying solely on the post-withdrawal balance check.

## Proof of Concept
**Natural rounding path (no attacker):**
1. Deploy `LRTWithdrawalManager` with Aave integration enabled.
2. Deposit ETH to Aave via normal protocol flow; `totalETHDepositedToAave = N`.
3. Withdraw all principal via `_withdrawFromAave`; due to Aave rounding, `totalETHDepositedToAave = 0` but `aaveAWETH.balanceOf(address(this)) = 1 wei`.
4. A user has a pending ETH withdrawal request where `contractBalance < request.expectedAssetAmount`.
5. Call `completeWithdrawal(ETH_TOKEN, ref)`:
   - `_withdrawFromAave(amountNeeded)` is called with `amountNeeded > 0`.
   - `aaveBalance = 1 wei > 0` → passes the `aaveBalance == 0` guard.
   - `withdrawablePrincipal = min(1 wei, 0) = 0`.
   - `withdrawnAmount = 0` → silent `return 0`.
   - `balanceAfter < request.expectedAssetAmount` → reverts with `InsufficientLiquidityForWithdrawal`.
6. All pending ETH withdrawals requiring Aave liquidity are blocked until admin recovery.

**Foundry test sketch:**
```solidity
function test_roundingResidual_freezesWithdrawal() public {
    // Set totalETHDepositedToAave = 0, mock aaveBalance = 1 wei
    vm.store(address(wm), totalETHDepositedToAaveSlot, bytes32(0));
    mockAWETH.setBalance(address(wm), 1);

    // Queue and unlock a 0.5 ETH withdrawal (contract ETH balance = 0)
    // Expect revert on completeWithdrawal
    vm.expectRevert(ILRTWithdrawalManager.InsufficientLiquidityForWithdrawal.selector);
    wm.completeWithdrawal(ETH_TOKEN, "ref");
}
```