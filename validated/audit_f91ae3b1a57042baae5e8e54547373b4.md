The code is confirmed exactly as described. Let me verify the full picture before rendering judgment.

Audit Report

## Title
ETH Deposit Limit Bypass Due to Missing `amount` in Cap Check - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` applies two different comparison logics: for LST assets it checks `totalAssetDeposits + amount > limit`, but for ETH it checks only `totalAssetDeposits > limit`, silently discarding the incoming `amount`. Any unprivileged caller can deposit ETH far exceeding the configured cap in a single transaction, as long as the cap has not already been breached by prior deposits.

## Finding Description
The root cause is the ETH-specific branch in `_checkIfDepositAmountExceedesCurrentLimit` (lines 676–682 of `contracts/LRTDepositPool.sol`):

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)); // amount ignored
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

The call chain is: `depositETH` (line 87) → `_beforeDeposit` (line 661) → `_checkIfDepositAmountExceedesCurrentLimit`. The guard at line 661–663 reverts only when the function returns `true`. For ETH, the function returns `true` only when `totalAssetDeposits` already exceeds the limit — the incoming `amount` is never added. In the normal operating state (`totalAssetDeposits = 0`, `limit = 100 ether`), a call with `msg.value = 10_000 ether` evaluates `0 > 100 ether` → `false`, so the deposit proceeds unchecked. No existing guard compensates for this omission.

## Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**

The `depositLimitByAsset` cap is a protocol-defined risk management constraint. The ETH branch fails to enforce it, meaning the contract does not deliver on its stated invariant. However, no direct theft, fund freezing, or provable insolvency follows from the bypass alone — the insolvency scenario requires speculative external conditions (EigenLayer slashing at a scale beyond protocol sizing) that are not demonstrated by the code path. The concrete, non-speculative impact is that the protocol accepts ETH deposits beyond its own configured ceiling.

## Likelihood Explanation
Any unprivileged user calling `depositETH` with an arbitrarily large `msg.value` triggers this in a single transaction. The only precondition is that `totalAssetDeposits` has not already exceeded the limit, which is the normal operating state. No special setup, flash loans, or privileged access is required.

## Recommendation
Remove the ETH-specific branch and apply the same amount-inclusive check uniformly:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

## Proof of Concept
1. Admin calls `LRTConfig.updateAssetDepositLimit(ETH_TOKEN, 100 ether)`.
2. Protocol is fresh: `getTotalAssetDeposits(ETH_TOKEN) == 0`.
3. Attacker calls `LRTDepositPool.depositETH{value: 10_000 ether}(0, "")`.
4. `_beforeDeposit` calls `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 10_000 ether)`.
5. ETH branch evaluates: `return (0 > 100 ether)` → `false`.
6. `_beforeDeposit` does not revert; `_mintRsETH` mints rsETH for the full `10_000 ether`.
7. The 100 ether cap is bypassed by 100×.

Foundry test sketch:
```solidity
function test_ethDepositCapBypass() public {
    vm.prank(admin);
    lrtConfig.updateAssetDepositLimit(LRTConstants.ETH_TOKEN, 100 ether);

    address attacker = makeAddr("attacker");
    vm.deal(attacker, 10_000 ether);
    vm.prank(attacker);
    lrtDepositPool.depositETH{value: 10_000 ether}(0, "");

    assertGt(getTotalAssetDeposits(LRTConstants.ETH_TOKEN), 100 ether);
}
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** contracts/LRTDepositPool.sol (L86-88)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

```

**File:** contracts/LRTDepositPool.sol (L661-663)
```text
        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }
```

**File:** contracts/LRTDepositPool.sol (L676-682)
```text
    function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (asset == LRTConstants.ETH_TOKEN) {
            return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
        }
        return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
    }
```
