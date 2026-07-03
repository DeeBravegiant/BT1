### Title
ETH Deposit Limit Check Omits New Deposit Amount, Allowing Limit Bypass — (File: `contracts/LRTDepositPool.sol`)

---

### Summary

In `LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit`, the ETH branch checks `totalAssetDeposits > depositLimit` without including the incoming deposit amount, while the non-ETH branch correctly checks `totalAssetDeposits + amount > depositLimit`. This inverted/incomplete comparison logic means the ETH deposit cap is effectively unenforced: any depositor can push total ETH holdings arbitrarily above the configured limit.

---

### Finding Description

`_checkIfDepositAmountExceedesCurrentLimit` is called from `_beforeDeposit` for every deposit, including ETH via `depositETH`. The function contains two branches:

```solidity
// contracts/LRTDepositPool.sol lines 676–682
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← missing `+ amount`
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

For every non-ETH LST the check is `totalAssetDeposits + amount > limit` — the prospective post-deposit total is compared against the cap. For ETH the check is `totalAssetDeposits > limit` — only the pre-deposit total is compared. The new deposit amount is never added, so the function returns `false` (i.e., "not exceeded") for any ETH deposit as long as the current total has not already crossed the limit, regardless of how large the incoming deposit is.

The caller in `_beforeDeposit` treats a `false` return as "safe to proceed":

```solidity
// contracts/LRTDepositPool.sol lines 661–663
if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
    revert MaximumDepositLimitReached();
}
```

So the revert that should protect the ETH cap is never triggered for a deposit that would push the total over the limit.

---

### Impact Explanation

The ETH deposit limit (`depositLimitByAsset[ETH_TOKEN]`) is a protocol safety mechanism that caps total ETH exposure in EigenLayer. With this bug, any unprivileged depositor can bypass it entirely: a single `depositETH` call can push total ETH holdings from just below the limit to an arbitrarily large value. This undermines the TVL cap, can cause over-allocation into EigenLayer strategies beyond their intended capacity, and violates the protocol's stated invariant that deposits are bounded. Impact: **contract fails to deliver promised safety guarantees / temporary freezing risk if downstream strategies hit their own caps**.

---

### Likelihood Explanation

The entry path is fully permissionless — `depositETH` is callable by any address with no role requirement. The condition is trivially reachable whenever `totalAssetDeposits` is at or near the configured limit, which is the normal operating state of a live protocol. No special timing, front-running, or privileged access is required.

---

### Recommendation

Add the incoming deposit amount to the ETH branch, matching the non-ETH branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

---

### Proof of Concept

1. Admin sets `depositLimitByAsset[ETH_TOKEN] = 1000 ether`.
2. Current `getTotalAssetDeposits(ETH_TOKEN)` returns `999 ether`.
3. Attacker calls `depositETH{value: 500 ether}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit` evaluates `999 ether > 1000 ether` → `false`.
5. Deposit proceeds; total ETH in protocol becomes `1499 ether`, exceeding the cap by `499 ether`.
6. For comparison, a `depositAsset` call with any LST at the same relative position would evaluate `999 ether + 500 ether > 1000 ether` → `true` → `revert MaximumDepositLimitReached()`. [1](#0-0) [2](#0-1)

### Citations

**File:** contracts/LRTDepositPool.sol (L661-669)
```text
        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
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
