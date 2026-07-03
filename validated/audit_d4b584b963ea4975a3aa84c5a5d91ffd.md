Audit Report

## Title
Unconditional Minimum rsETH Check Traps Sub-Threshold Dust After Partial Withdrawals - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`LRTWithdrawalManager` enforces `minRsEthAmountToWithdraw[asset]` unconditionally in both `initiateWithdrawal` and `instantWithdrawal` with no exception for a user withdrawing their entire remaining balance. Any user who makes a partial withdrawal leaving a remainder below the configured minimum will be unable to redeem that remainder through any on-chain path until they acquire additional rsETH from the open market, constituting a temporary freeze of their funds.

## Finding Description
`minRsEthAmountToWithdraw[asset]` is stored as a per-asset mapping and set by the admin via `setMinRsEthAmountToWithdraw`. [1](#0-0) 

Both withdrawal entry points apply the same unconditional guard:

`initiateWithdrawal` (line 162): [2](#0-1) 

`instantWithdrawal` (line 224): [3](#0-2) 

Neither check contains a bypass for the case where `rsETHUnstaked` equals the caller's full rsETH balance. There is no third withdrawal path in the contract. Once a user's balance falls below the threshold — a natural outcome of any partial withdrawal — both entry points revert with `InvalidAmountToWithdraw` and the user has no on-chain recourse within this contract.

The admin function that sets the minimum has no upper bound and can be set to any non-zero value: [4](#0-3) 

## Impact Explanation
**Medium — Temporary freezing of funds.**

The trapped rsETH represents a proportional claim on underlying ETH/LST assets held by the protocol. The user cannot redeem it through any on-chain path while their balance remains below the minimum. The freeze is temporary only in the sense that the user can purchase additional rsETH on the open market to bring their balance back above the threshold, but this imposes an unintended cost and market-exposure risk on the user for a situation they did not create through any erroneous action.

## Likelihood Explanation
**Medium.**

The scenario requires no special permissions, no front-running, and no external protocol failure. Any user who makes a partial withdrawal — a completely normal and expected action — is at risk. The minimum is admin-configurable to any value; even a modest minimum (e.g., 0.1 ETH equivalent) can trap meaningful dust for users who withdraw most but not all of their balance. The condition is repeatable and affects all assets for which a non-zero minimum is configured.

## Recommendation
Add a full-balance exception to the minimum check in both `initiateWithdrawal` and `instantWithdrawal`:

```solidity
uint256 userBalance = IERC20(lrtConfig.rsETH()).balanceOf(msg.sender);
if (rsETHUnstaked == 0 ||
    (rsETHUnstaked < minRsEthAmountToWithdraw[asset] && rsETHUnstaked != userBalance)) {
    revert InvalidAmountToWithdraw();
}
```

This allows a user to always withdraw their entire remaining balance regardless of the configured minimum, eliminating the dust-trapping scenario while preserving the minimum for all partial withdrawals.

## Proof of Concept

1. Admin calls `setMinRsEthAmountToWithdraw(ETH, 1e18)` — minimum set to 1 rsETH.
2. Alice deposits 2 ETH via `LRTDepositPool.depositETH`, receiving 2 rsETH.
3. Alice calls `initiateWithdrawal(ETH, 1.5e18, "")`. Check: `1.5e18 >= 1e18` → passes. Alice now holds 0.5 rsETH.
4. Alice calls `initiateWithdrawal(ETH, 0.5e18, "")`. Check: `0.5e18 < 1e18` → reverts `InvalidAmountToWithdraw`.
5. Alice calls `instantWithdrawal(ETH, 0.5e18, "")`. Same check at line 224 → reverts `InvalidAmountToWithdraw`.
6. Alice's 0.5 rsETH (≈0.5 ETH of value) is frozen. She must acquire ≥0.5 rsETH from the open market and combine it to reach the 1 rsETH minimum before either withdrawal path becomes available.

**Foundry test sketch:**
```solidity
function test_dustTrap() public {
    vm.prank(admin);
    withdrawalManager.setMinRsEthAmountToWithdraw(ETH, 1e18);

    vm.startPrank(alice);
    depositPool.depositETH{value: 2e18}(""); // Alice gets 2 rsETH
    rsETH.approve(address(withdrawalManager), type(uint256).max);

    withdrawalManager.initiateWithdrawal(ETH, 1.5e18, ""); // succeeds
    // Alice now has 0.5 rsETH

    vm.expectRevert(ILRTWithdrawalManager.InvalidAmountToWithdraw.selector);
    withdrawalManager.initiateWithdrawal(ETH, 0.5e18, ""); // reverts

    vm.expectRevert(ILRTWithdrawalManager.InvalidAmountToWithdraw.selector);
    withdrawalManager.instantWithdrawal(ETH, 0.5e18, ""); // reverts
    vm.stopPrank();
}
```

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L35-35)
```text
    mapping(address asset => uint256) public minRsEthAmountToWithdraw;
```

**File:** contracts/LRTWithdrawalManager.sol (L162-164)
```text
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }
```

**File:** contracts/LRTWithdrawalManager.sol (L224-226)
```text
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }
```

**File:** contracts/LRTWithdrawalManager.sol (L330-332)
```text
    function setMinRsEthAmountToWithdraw(address asset, uint256 minRsEthAmountToWithdraw_) external onlyLRTAdmin {
        minRsEthAmountToWithdraw[asset] = minRsEthAmountToWithdraw_;
        emit MinAmountToWithdrawUpdated(asset, minRsEthAmountToWithdraw_);
```
