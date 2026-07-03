Audit Report

## Title
`LRTDepositPool._beforeDeposit` — Remaining LST Deposit Capacity Becomes Permanently Inaccessible When `depositLimit - totalDeposits < minAmountToDeposit` - (File: contracts/LRTDepositPool.sol)

## Summary
`_beforeDeposit` enforces a minimum deposit floor (`minAmountToDeposit`) and a per-asset ceiling (`depositLimitByAsset`). When the remaining capacity for an LST asset falls below `minAmountToDeposit`, no valid deposit amount exists: amounts at or above the minimum exceed the ceiling, and amounts below the minimum are rejected by the floor. The remaining capacity is permanently inaccessible via the public deposit path until an admin adjusts configuration, while `getAssetCurrentLimit` continues to advertise a non-zero available capacity.

## Finding Description
`_beforeDeposit` (lines 648–670) applies two sequential checks:

```solidity
// L657-658
if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
    revert InvalidAmountToDeposit();
}
// L661-663
if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
    revert MaximumDepositLimitReached();
}
```

For LST assets, `_checkIfDepositAmountExceedesCurrentLimit` (lines 676–682) evaluates:

```solidity
return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
```

When `depositLimitByAsset[asset] - totalAssetDeposits < minAmountToDeposit`:
- Any `depositAmount >= minAmountToDeposit` causes `totalAssetDeposits + depositAmount > depositLimitByAsset` → `MaximumDepositLimitReached`
- Any `depositAmount < minAmountToDeposit` → `InvalidAmountToDeposit`

No valid deposit amount exists. Meanwhile, `getAssetCurrentLimit` (lines 402–409) returns `depositLimitByAsset - totalAssetDeposits`, a non-zero value, falsely advertising available capacity.

## Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.** The deposit pool cannot be filled to its stated limit for the affected LST asset. Users who attempt to deposit are blocked even though the protocol's own accounting (`getAssetCurrentLimit`) reports available capacity. No funds are lost, but the contract fails to deliver its promised deposit functionality for the remaining capacity.

## Likelihood Explanation
No malicious actor is required. As deposits for a given LST accumulate organically toward the per-asset limit, the remaining capacity will eventually fall below `minAmountToDeposit`. `minAmountToDeposit` is a single global value applied to all assets, while `depositLimitByAsset` is per-asset and set independently, making misalignment likely over time. The condition is not self-healing and persists until an admin lowers `minAmountToDeposit` or raises `depositLimitByAsset`.

## Recommendation
In `_beforeDeposit`, when `depositAmount >= minAmountToDeposit` but `totalAssetDeposits + depositAmount > depositLimitByAsset`, accept the deposit capped at the remaining capacity (`depositLimitByAsset - totalAssetDeposits`) provided the capped amount is non-zero. Alternatively, enforce at configuration time (in both `setMinAmountToDeposit` and `updateAssetDepositLimit`) that `depositLimitByAsset[asset] - currentTotalDeposits >= minAmountToDeposit` so the stuck state can never be entered.

## Proof of Concept
**Setup:**
- `minAmountToDeposit = 1e18`
- `depositLimitByAsset[stETH] = 100e18`
- `totalAssetDeposits(stETH) = 99.5e18`
- `getAssetCurrentLimit(stETH)` returns `0.5e18`

**Attempt 1 — deposit `1e18` (at minimum):**
```
99.5e18 + 1e18 = 100.5e18 > 100e18 → MaximumDepositLimitReached
```

**Attempt 2 — deposit `0.5e18` (exactly the remaining capacity):**
```
0.5e18 < 1e18 (minAmountToDeposit) → InvalidAmountToDeposit
```

Both revert. `getAssetCurrentLimit(stETH)` still returns `0.5e18`. The pool is stuck until admin intervention.

**Foundry test plan:**
```solidity
function test_depositStuck() public {
    // set minAmountToDeposit = 1e18, depositLimit = 100e18
    // deposit 99.5e18 from a user
    // assert getAssetCurrentLimit() == 0.5e18
    // attempt deposit of 1e18 → expect revert MaximumDepositLimitReached
    // attempt deposit of 0.5e18 → expect revert InvalidAmountToDeposit
}
```