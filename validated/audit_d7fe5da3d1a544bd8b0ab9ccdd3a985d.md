### Title
ETH Deposit Limit Check Omits New Deposit Amount, Allowing Any Depositor to Exceed the Configured Cap — (File: `contracts/LRTDepositPool.sol`)

---

### Summary

`_checkIfDepositAmountExceedesCurrentLimit` in `LRTDepositPool` applies an asymmetric check: for LST assets it correctly adds the incoming `amount` to the running total before comparing against the limit, but for ETH it checks only the pre-deposit total. Any depositor can therefore push total ETH deposits above the admin-configured cap in a single call.

---

### Finding Description

The function reads:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)); // ← amount not added
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct
}
``` [1](#0-0) 

For every LST the guard is `totalAssetDeposits + amount > limit`, which prevents the limit from being crossed. For ETH the guard is `totalAssetDeposits > limit`, which only fires if the limit was **already** exceeded before the deposit. A depositor who arrives when `totalAssetDeposits == limit - 1 wei` passes the check and can deposit an arbitrarily large amount of ETH, because `msg.value` is never added to the comparison.

The check is invoked unconditionally from `_beforeDeposit`, which is the sole gate for `depositETH`:

```solidity
uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
``` [2](#0-1) 

There is no secondary enforcement of the ETH cap anywhere in the deposit path.

---

### Impact Explanation

The ETH deposit limit is a safety parameter set by the `MANAGER` role to bound the protocol's ETH exposure (e.g., EigenLayer strategy capacity, liquidity risk). Bypassing it means the protocol silently accepts more ETH than the configured ceiling, violating the invariant the limit is meant to enforce. No funds are directly stolen, but the protocol fails to deliver the promised deposit-cap guarantee.

**Impact: Low — Contract fails to deliver promised returns.**

---

### Likelihood Explanation

The condition is trivially reachable: any caller of `depositETH` when `totalAssetDeposits ≤ depositLimit` can trigger it. No special role, front-running, or unusual state is required. The normal operating state of the protocol (deposits below the cap) is exactly the precondition.

**Likelihood: High.**

---

### Recommendation

Add `amount` to the ETH branch, mirroring the LST branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

---

### Proof of Concept

1. Admin sets ETH deposit limit to 1 000 ETH via `updateAssetDepositLimit`.
2. Protocol accumulates 999.9 ETH across the pool, NDCs, and EigenLayer.
3. Attacker calls `depositETH{value: 500 ether}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit` evaluates `999.9 ETH > 1000 ETH` → `false` → no revert.
5. 500 ETH is accepted; total ETH deposits become 1 499.9 ETH — 50 % above the configured cap.
6. The limit is now permanently exceeded; subsequent honest depositors are blocked while the attacker's over-deposit remains in the protocol.

### Citations

**File:** contracts/LRTDepositPool.sol (L87-87)
```text
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
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
