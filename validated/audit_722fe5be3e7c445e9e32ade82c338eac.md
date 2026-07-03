Audit Report

## Title
ETH Deposit Limit Bypass via Missing `amount` in `_checkIfDepositAmountExceedesCurrentLimit` - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` applies an asymmetric check: for ERC20 LST assets it correctly evaluates `totalAssetDeposits + amount > limit`, but for native ETH it evaluates only `totalAssetDeposits > limit`, ignoring the incoming deposit amount entirely. Any unprivileged caller can therefore deposit an arbitrarily large amount of ETH in a single `depositETH` call and push total ETH deposits well above the configured `depositLimitByAsset[ETH_TOKEN]` cap.

## Finding Description
The guard function at `contracts/LRTDepositPool.sol` lines 676–682 branches on `asset == LRTConstants.ETH_TOKEN`:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // amount ignored
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // correct
}
```

The ETH branch never adds `amount` to `totalAssetDeposits`. As long as the current total is at or below the limit, the function returns `false` regardless of `msg.value`, so `_beforeDeposit` (lines 648–670) never reverts with `MaximumDepositLimitReached`. The public entry point `depositETH` (lines 76–93) is callable by any user with any `msg.value`, and it passes `msg.value` as `depositAmount` directly into `_beforeDeposit`.

## Impact Explanation
The `depositLimitByAsset` cap is the protocol's primary mechanism for bounding ETH intake. Bypassing it allows unlimited rsETH to be minted against ETH deposits beyond the intended ceiling. This concretely maps to **Low – contract fails to deliver promised returns**: the deposit cap invariant is broken and the protocol accepts more ETH than intended. It also creates a path to **Medium – temporary freezing of funds**: excess ETH that cannot be forwarded to EigenLayer strategies remains stranded in the deposit pool or NDCs with no downstream restaking destination, and the rsETH minted against it may not be fully backed by restaked positions.

## Likelihood Explanation
The exploit path is fully permissionless. No special role, privileged key, front-running, or external dependency is required. Any user who calls `depositETH{value: X}(0, "")` where `X` exceeds the remaining cap triggers the bug. The condition is reachable whenever the ETH deposit limit is set to a finite value and the pool is not paused.

## Recommendation
Remove the ETH-specific branch and unify the check to always include `amount`:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

This matches the existing ERC20 logic and closes the asymmetry.

## Proof of Concept
1. Admin sets `depositLimitByAsset[ETH_TOKEN] = 1000 ether`.
2. Existing deposits bring `getTotalAssetDeposits(ETH_TOKEN)` to exactly `1000 ether`.
3. Attacker calls `depositETH{value: 500 ether}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 500 ether)` evaluates `1000 ether > 1000 ether` → `false` → guard passes.
5. `_mintRsETH` mints rsETH for 500 ETH; total ETH deposits become `1500 ether`, 50% above the cap.
6. For comparison, `depositAsset(stETH, 500 ether, ...)` at the same state evaluates `1000 ether + 500 ether > 1000 ether` → `true` → correctly reverts with `MaximumDepositLimitReached`.

**Foundry test plan:**
```solidity
function test_ethDepositBypassesLimit() public {
    // set ETH deposit limit to 1000 ether, fill to exactly 1000 ether via prior deposits
    // assert getTotalAssetDeposits(ETH_TOKEN) == 1000 ether
    // call depositETH{value: 500 ether}(0, "") from unprivileged address
    // assert call succeeds (no revert)
    // assert getTotalAssetDeposits(ETH_TOKEN) == 1500 ether  // limit breached
    // assert depositAsset(stETH, 500 ether, ...) reverts with MaximumDepositLimitReached
}
```