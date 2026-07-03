### Title
Deposit Pool Can Be Griefed Into Unusable State by Leaving Dust Below `minAmountToDeposit` - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool._beforeDeposit` enforces both a minimum deposit floor (`minAmountToDeposit`) and a per-asset ceiling (`depositLimitByAsset`). An unprivileged depositor can craft a deposit that leaves the remaining capacity below `minAmountToDeposit`, permanently blocking further deposits for that asset until admin intervention.

### Finding Description
`_beforeDeposit` applies two sequential guards:

```solidity
// LRTDepositPool.sol L657
if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
    revert InvalidAmountToDeposit();
}

// LRTDepositPool.sol L661
if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
    revert MaximumDepositLimitReached();
}
```

`_checkIfDepositAmountExceedesCurrentLimit` for LST assets evaluates:

```solidity
// LRTDepositPool.sol L681
return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
```

Let `R = depositLimitByAsset[asset] - totalAssetDeposits` be the remaining capacity. If an attacker deposits exactly `R - (minAmountToDeposit - 1)`, the new remaining capacity becomes `minAmountToDeposit - 1`. After this:

- Any deposit `>= minAmountToDeposit` fails: `totalAssetDeposits + amount > depositLimitByAsset` → `MaximumDepositLimitReached`
- Any deposit `< minAmountToDeposit` fails: `depositAmount < minAmountToDeposit` → `InvalidAmountToDeposit`

No valid deposit amount exists. The asset's deposit pool is bricked until an admin calls `updateAssetDepositLimit` (in `LRTConfig.sol`) or `setMinAmountToDeposit` (in `LRTDepositPool.sol`).

### Impact Explanation
**Medium — Temporary freezing of funds.**

All future deposits for the targeted LST asset (e.g., stETH, ETHx) are blocked. Users cannot deposit into the protocol for that asset. The protocol cannot accumulate more TVL for that asset. Recovery requires privileged admin action (`updateAssetDepositLimit` or `setMinAmountToDeposit`), which is not instant and may require governance/timelock delays. The attacker can repeat the attack each time the limit is raised.

### Likelihood Explanation
The attack is cheap and permissionless. The attacker only needs to call `depositAsset` with a precisely calculated amount. No special role, flash loan, or oracle manipulation is required. The attacker's deposited funds are not lost — they receive rsETH in return. The attack can be repeated after each admin remediation, making it a persistent griefing vector.

### Recommendation
In `_checkIfDepositAmountExceedesCurrentLimit`, also allow a deposit that exactly fills the remaining capacity, and additionally, when the remaining capacity is less than `minAmountToDeposit`, allow a deposit of exactly that remaining amount (bypassing the minimum floor for the final fill):

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    uint256 limit = lrtConfig.depositLimitByAsset(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > limit);
    }
    uint256 remaining = totalAssetDeposits >= limit ? 0 : limit - totalAssetDeposits;
    // Allow a deposit that exactly fills the remaining dust below minAmountToDeposit
    if (remaining < minAmountToDeposit && amount == remaining) {
        return false;
    }
    return (totalAssetDeposits + amount > limit);
}
```

Alternatively, enforce in `_beforeDeposit` that if the remaining capacity is below `minAmountToDeposit`, the deposit must equal exactly the remaining capacity.

### Proof of Concept

1. `depositLimitByAsset[stETH] = 100_000 ether`, `minAmountToDeposit = 0.001 ether`
2. `totalAssetDeposits(stETH) = 99_990 ether` (current state)
3. Attacker calls `depositAsset(stETH, 9_999.9991 ether, ...)` — this is valid: `99_990 + 9_999.9991 = 99_999.9991 ether`, which is `<= 100_000 ether`, and `9_999.9991 ether >= 0.001 ether`
4. Remaining capacity: `100_000 - 99_999.9991 = 0.0009 ether`, which is `< minAmountToDeposit (0.001 ether)`
5. Any subsequent depositor calling `depositAsset(stETH, 0.001 ether, ...)` reverts: `99_999.9991 + 0.001 = 100_000.0001 > 100_000` → `MaximumDepositLimitReached`
6. Any subsequent depositor calling `depositAsset(stETH, 0.0009 ether, ...)` reverts: `0.0009 < 0.001` → `InvalidAmountToDeposit`
7. The stETH deposit pool is frozen until admin raises the limit or lowers the minimum. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/LRTDepositPool.sol (L30-30)
```text
    uint256 public minAmountToDeposit;
```

**File:** contracts/LRTDepositPool.sol (L282-284)
```text
    function setMinAmountToDeposit(uint256 minAmountToDeposit_) external onlyLRTAdmin {
        minAmountToDeposit = minAmountToDeposit_;
        emit MinAmountToDepositUpdated(minAmountToDeposit_);
```

**File:** contracts/LRTDepositPool.sol (L657-662)
```text
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
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

**File:** contracts/LRTConfig.sol (L23-23)
```text
    mapping(address token => uint256 amount) public depositLimitByAsset;
```
