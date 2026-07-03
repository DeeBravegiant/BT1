Audit Report

## Title
`_processWithdrawalCompletion` Skips `_collectInterestToTreasury()`, Causing ETH Withdrawals to Revert When `totalETHDepositedToAave` Reaches Zero — (`contracts/LRTWithdrawalManager.sol`)

## Summary

`_withdrawFromAave` caps the withdrawable amount at `min(aaveBalance, totalETHDepositedToAave)`. When the last principal withdrawal drives `totalETHDepositedToAave` to zero while Aave still holds accrued interest, every subsequent ETH `completeWithdrawal` that needs to pull from Aave silently receives 0 ETH and reverts with `InsufficientLiquidityForWithdrawal`. Unlike every other call site, `_processWithdrawalCompletion` never calls `_collectInterestToTreasury()` before `_withdrawFromAave`, making this outcome inevitable whenever any interest has accrued.

## Finding Description

**Root cause — `_withdrawFromAave` principal cap** (`LRTWithdrawalManager.sol` lines 912–915):

```solidity
uint256 withdrawablePrincipal =
    aaveBalance < totalETHDepositedToAave ? aaveBalance : totalETHDepositedToAave;
withdrawnAmount = amount > withdrawablePrincipal ? withdrawablePrincipal : amount;
if (withdrawnAmount == 0) return 0;   // silent zero-return
```

When `totalETHDepositedToAave == 0`, `withdrawablePrincipal = 0` regardless of `aaveBalance`, and the function returns 0 silently.

**Trigger — `_processWithdrawalCompletion` never collects interest first** (lines 720–731):

```solidity
if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN) {
    uint256 contractBalance = address(this).balance;
    if (contractBalance < request.expectedAssetAmount) {
        uint256 amountNeeded = request.expectedAssetAmount - contractBalance;
        _withdrawFromAave(amountNeeded);          // no _collectInterestToTreasury() call
        uint256 balanceAfter = address(this).balance;
        if (balanceAfter < request.expectedAssetAmount) {
            revert InsufficientLiquidityForWithdrawal();
        }
    }
}
```

Every other caller of `_withdrawFromAave` — `emergencyWithdrawFromAave` (line 558), `setAaveIntegrationEnabled` (line 490), and `configureAaveIntegration` (line 442) — calls `_collectInterestToTreasury()` first. `_processWithdrawalCompletion` is the sole exception.

**Concrete state transition:**

| Step | `totalETHDepositedToAave` | `aaveBalance` |
|------|--------------------------|---------------|
| 100 ETH deposited | 100 | 100 |
| Interest accrues | 100 | 101 |
| User A completes 100 ETH withdrawal: `min(101,100)=100` withdrawn | **0** | **1** |
| User B tries to complete 1 ETH withdrawal: `min(1,0)=0` → returns 0 | 0 | 1 |
| `balanceAfter < 1` → `revert InsufficientLiquidityForWithdrawal` | — | — |

**Recovery paths are broken:**

- `collectInterestToTreasury()` (operator-only) can drain the residual interest to treasury, but this does not credit waiting users and leaves them still blocked.
- `emergencyWithdrawFromAave`: calls `_collectInterestToTreasury()` first (draining `aaveBalance` to 0), then calls `_withdrawFromAave(amount)` which hits `if (aaveBalance == 0) revert InsufficientAaveBalance()` at line 909 — the entire transaction reverts, so even this path fails.
- The only unblock path is an operator manually depositing fresh ETH to the contract, which is not permissionless.

## Impact Explanation

**Medium — Temporary freezing of user funds**: All ETH `completeWithdrawal` calls that require pulling from Aave revert with `InsufficientLiquidityForWithdrawal` until an operator manually deposits fresh ETH into the contract. No permissionless recovery path exists.

**Medium — Permanent freezing of unclaimed yield**: The accrued Aave interest is permanently inaccessible for user withdrawals. It can only be routed to treasury via `collectInterestToTreasury()`, never credited to the users whose withdrawals are blocked.

## Likelihood Explanation

No attacker is required. This is a natural, inevitable consequence of normal protocol operation. Aave interest accrues continuously from the moment ETH is deposited. Whenever the last principal withdrawal completes — a routine event — `totalETHDepositedToAave` reaches zero while `aaveBalance > 0`. Any subsequent ETH `completeWithdrawal` that needs to pull from Aave will revert. The likelihood is high.

## Recommendation

In `_processWithdrawalCompletion`, call `_collectInterestToTreasury()` before `_withdrawFromAave`, consistent with every other call site:

```solidity
if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN) {
    uint256 contractBalance = address(this).balance;
    if (contractBalance < request.expectedAssetAmount) {
        _collectInterestToTreasury();                      // collect interest first
        uint256 amountNeeded = request.expectedAssetAmount - contractBalance;
        _withdrawFromAave(amountNeeded);
        uint256 balanceAfter = address(this).balance;
        if (balanceAfter < request.expectedAssetAmount) {
            revert InsufficientLiquidityForWithdrawal();
        }
    }
}
```

Alternatively, guard against `totalETHDepositedToAave` underflowing below the remaining `aaveBalance`, or allow `_withdrawFromAave` to use the full `aaveBalance` when `totalETHDepositedToAave == 0`.

## Proof of Concept

```solidity
// Foundry fork test (Ethereum mainnet fork)
// forge test --match-test test_interestFreezesWithdrawals -vvv

function test_interestFreezesWithdrawals() public {
    // 1. Operator deposits 100 ETH to Aave
    vm.deal(address(wm), 100 ether);
    vm.prank(operator);
    wm.depositIdleETHToAave(100 ether);
    // totalETHDepositedToAave = 100, aaveBalance ≈ 100

    // 2. Advance time to accrue interest
    vm.warp(block.timestamp + 365 days);
    // aaveBalance ≈ 101 (1 ETH interest)

    // 3. User A completes unlocked withdrawal for 100 ETH
    // _withdrawFromAave(100): withdrawablePrincipal = min(101,100) = 100
    // totalETHDepositedToAave → 0, aaveBalance → 1
    vm.prank(userA);
    wm.completeWithdrawal(ETH_TOKEN, "");

    // 4. User B tries to complete unlocked withdrawal for 1 ETH
    // _withdrawFromAave(1): withdrawablePrincipal = min(1,0) = 0 → returns 0
    // balanceAfter < 1 → revert InsufficientLiquidityForWithdrawal
    vm.prank(userB);
    vm.expectRevert(ILRTWithdrawalManager.InsufficientLiquidityForWithdrawal.selector);
    wm.completeWithdrawal(ETH_TOKEN, "");
    // 1 ETH of interest remains locked in Aave, inaccessible to users
}
```