### Title
ETH Deposit Limit Check Excludes Deposit Amount, Allowing Bypass of `depositLimitByAsset` - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit` applies an asymmetric deposit-limit check: for LST assets it correctly tests `totalAssetDeposits + amount > limit`, but for ETH it only tests `totalAssetDeposits > limit`, omitting the incoming deposit amount. Any unprivileged depositor can therefore push total ETH deposits arbitrarily above the configured cap in a single call to `depositETH`.

### Finding Description
`_checkIfDepositAmountExceedesCurrentLimit` (contracts/LRTDepositPool.sol, lines 676–682) branches on the asset type:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← amount excluded
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← amount included
}
```

For LST assets the guard is `totalDeposits + amount > limit`, which correctly blocks any deposit that would breach the cap. For ETH the guard is `totalDeposits > limit`, which only reverts when the cap has **already** been exceeded. A deposit that would push the total from just-below-limit to far-above-limit passes the check without error.

Both `depositETH` and `depositAsset` route through `_beforeDeposit`, which calls this function. The restriction that is correctly applied to every LST deposit is therefore absent for every ETH deposit.

### Impact Explanation
The `depositLimitByAsset` cap is the protocol's primary risk-management control over how much of each asset it accepts into EigenLayer restaking. Bypassing it for ETH allows the protocol to accumulate unbounded ETH exposure beyond the intended ceiling, violating the invariant the admin configured. No funds are directly stolen, but the contract fails to deliver the promised deposit-cap guarantee.

**Impact: Low** — Contract fails to deliver promised returns (deposit cap enforcement), but deposited funds are not lost.

### Likelihood Explanation
The entry point `depositETH` is public and payable with no access control beyond `whenNotPaused`. Any depositor can exploit this in a single transaction whenever `totalAssetDeposits ≤ depositLimitByAsset`. No special role, front-running, or external dependency is required.

**Likelihood: High**

### Recommendation
Include the deposit amount in the ETH branch, matching the LST branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

### Proof of Concept
1. Admin sets `depositLimitByAsset[ETH_TOKEN] = 1000 ether`.
2. Current `getTotalAssetDeposits(ETH_TOKEN)` returns `999 ether`.
3. Attacker calls `depositETH{value: 500 ether}(minRSETH, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit` evaluates `999e18 > 1000e18` → `false` → no revert.
5. `_mintRsETH` mints rsETH for the full 500 ETH.
6. Total ETH deposits are now `1499 ether`, exceeding the 1000 ETH cap by 499 ETH.

The same call with an LST would have evaluated `999e18 + 500e18 > 1000e18` → `true` → `MaximumDepositLimitReached` revert, demonstrating the asymmetry.