### Title
ETH Deposit Limit Check Omits Deposit Amount, Allowing Limit to Be Exceeded - (File: contracts/LRTDepositPool.sol)

---

### Summary

`_checkIfDepositAmountExceedesCurrentLimit()` in `LRTDepositPool.sol` applies an inconsistent comparison for ETH versus ERC20 tokens. For ETH it omits the incoming deposit amount from the comparison, so the deposit limit can be silently exceeded by any depositor.

---

### Finding Description

The function `_checkIfDepositAmountExceedesCurrentLimit` is the sole gate that enforces the per-asset deposit cap before minting rsETH. [1](#0-0) 

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← amount missing
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct for ERC20
}
```

For every ERC20 asset the check is `totalAssetDeposits + amount > limit`, which correctly blocks a deposit that would push the total over the cap.

For ETH the check is `totalAssetDeposits > limit`, which only blocks a deposit when the cap has **already** been exceeded. At the exact moment `totalAssetDeposits == limit` the expression evaluates to `false`, the deposit is accepted, and the total is pushed above the cap by the full deposit amount.

This is the same root-cause class as the reported issue: a comparison that uses the wrong operand (the pre-deposit state instead of the post-deposit state), causing the guard to be ineffective.

The function is called unconditionally from `_beforeDeposit`, which is called by both `depositETH` and `depositAsset`. [2](#0-1) 

---

### Impact Explanation

The ETH deposit cap set by the admin via `updateAssetDepositLimit` is not enforced for the final deposit that reaches the limit. Any depositor can push total ETH deposits above the configured cap by exactly their deposit amount. The protocol fails to deliver its promised deposit-limit guarantee for ETH while no funds are directly stolen.

**Impact: Low — Contract fails to deliver promised returns, but doesn't lose value.**

---

### Likelihood Explanation

The condition is triggered naturally as the protocol fills up: the moment cumulative ETH deposits equal the configured limit, the very next ETH deposit (of any size) bypasses the cap. No special privileges, front-running, or coordination are required. Any ordinary depositor calling `depositETH` at that moment triggers it.

---

### Recommendation

Apply the same formula used for ERC20 tokens to the ETH branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

This mirrors the fix described in the referenced report: compare against the **input** (the new total after the deposit) rather than the current state alone.

---

### Proof of Concept

1. Admin calls `updateAssetDepositLimit(ETH_TOKEN, 100 ether)` — cap is 100 ETH.
2. Users deposit until `getTotalAssetDeposits(ETH_TOKEN) == 100 ether`.
3. Attacker calls `depositETH{value: 50 ether}(...)`.
4. Inside `_checkIfDepositAmountExceedesCurrentLimit`:
   - `totalAssetDeposits = 100 ether`
   - ETH branch: `100 ether > 100 ether` → `false` → deposit is **not** blocked.
5. 50 ETH is accepted; `getTotalAssetDeposits(ETH_TOKEN)` is now 150 ETH — 50 ETH above the cap.
6. For comparison, an ERC20 deposit at the same state would evaluate `100 ether + 50 ether > 100 ether` → `true` → correctly reverted.

### Citations

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
