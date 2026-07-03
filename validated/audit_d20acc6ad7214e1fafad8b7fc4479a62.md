The code at lines 676-682 of `contracts/LRTDepositPool.sol` confirms the claim exactly. The ETH branch omits `amount` from the comparison while the ERC-20 branch correctly includes it.

Audit Report

## Title
ETH Deposit Cap Bypass via Missing `amount` in Limit Check - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` evaluates the ETH branch as `totalAssetDeposits > depositLimit` instead of `totalAssetDeposits + amount > depositLimit`. While the current total is at or below the cap — the normal operating state — the check unconditionally returns `false` regardless of the incoming deposit size, allowing any caller to push ETH deposits arbitrarily beyond the configured limit in a single transaction.

## Finding Description
In `contracts/LRTDepositPool.sol` at lines 676-682:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // amount ignored
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

The ETH branch answers "is the pool already over the limit?" rather than "would this deposit exceed the limit?" The ERC-20 branch at line 681 correctly adds `amount`. The call chain is: `depositETH` (L76) → `_beforeDeposit` (L648) → `_checkIfDepositAmountExceedesCurrentLimit` (L676). `_beforeDeposit` reverts with `MaximumDepositLimitReached` only when the function returns `true` (L661-663), but for ETH it never returns `true` while `totalAssetDeposits ≤ depositLimit`, so no revert occurs regardless of `amount`.

## Impact Explanation
**Low — Contract fails to deliver promised returns.**
The deposit limit is the protocol's primary on-chain safety valve for ETH exposure. Bypassing it allows a single depositor to push `getTotalAssetDeposits(ETH)` from just below the cap to an arbitrarily large value, minting rsETH beyond the intended cap and causing the protocol to operate outside its configured risk envelope. Because deposited ETH is real collateral backing minted rsETH, there is no direct fund theft, but the protocol violates its own risk parameters, which can escalate under EigenLayer slashing if the cap was sized to bound that exposure.

## Likelihood Explanation
**High.** The entry point is the public, permissionless, payable `depositETH` function (L76-93). No special role, flash loan, or price manipulation is required. The precondition — `totalAssetDeposits(ETH) ≤ depositLimit` — is the normal operating state of the protocol. Any ETH holder can exploit this at any time.

## Recommendation
Add `amount` to the ETH branch to match the ERC-20 branch:

```diff
    if (asset == LRTConstants.ETH_TOKEN) {
-       return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
+       return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
    }
```

## Proof of Concept
1. Admin sets `depositLimitByAsset(ETH_TOKEN) = 100 ether`.
2. Protocol holds `totalAssetDeposits(ETH) = 99 ether`.
3. Attacker calls `depositETH{value: 1000 ether}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit(ETH, 1000e18)` evaluates `99e18 > 100e18` → `false` → no revert.
5. 1000 ETH is accepted; `getTotalAssetDeposits(ETH)` becomes 1099 ETH — 10× the cap.
6. Subsequent calls revert because `1099e18 > 100e18` is now `true`, but the cap has already been violated by 999 ETH.

**Foundry test plan:**
```solidity
function test_ethDepositCapBypass() public {
    vm.prank(admin);
    lrtConfig.setDepositLimitByAsset(ETH_TOKEN, 100 ether);
    // seed pool to 99 ether via legitimate deposits
    // ...
    vm.deal(attacker, 1000 ether);
    vm.prank(attacker);
    lrtDepositPool.depositETH{value: 1000 ether}(0, "");
    assertGt(lrtDepositPool.getTotalAssetDeposits(ETH_TOKEN), 100 ether);
}
```