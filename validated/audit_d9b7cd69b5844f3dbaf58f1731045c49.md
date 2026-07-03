Audit Report

## Title
ETH Deposit Limit Bypass Due to Missing `amount` in Boundary Check - (`contracts/LRTDepositPool.sol`)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` applies an asymmetric comparison: the ETH branch checks `totalAssetDeposits > depositLimit` while the non-ETH branch correctly checks `totalAssetDeposits + amount > depositLimit`. When `totalAssetDeposits == depositLimit` (cap exactly reached), the ETH branch returns `false`, allowing one additional deposit to push the total above the configured ceiling. This breaks the protocol invariant that `getTotalAssetDeposits(ETH) ≤ depositLimitByAsset(ETH)` at all times.

## Finding Description
In `contracts/LRTDepositPool.sol` at lines 676–682, `_checkIfDepositAmountExceedesCurrentLimit` branches on asset type:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)); // missing + amount
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

The ETH branch evaluates only whether the **current** total already exceeds the limit, not whether the **post-deposit** total would. `_beforeDeposit` (lines 648–670) calls this function and reverts with `MaximumDepositLimitReached` only when it returns `true`. At the exact boundary where `totalAssetDeposits == depositLimit`, `totalAssetDeposits > depositLimit` is `false`, so no revert occurs. `depositETH` (lines 76–93) then proceeds to call `_mintRsETH`, minting rsETH and recording the deposit, pushing `getTotalAssetDeposits(ETH)` above the cap. No other guard in `depositETH` or `_beforeDeposit` catches this case.

## Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**

The `depositLimitByAsset` cap is the protocol's primary risk-management control over ETH accepted into EigenLayer restaking. When the cap is exactly reached, any depositor can still push one additional ETH deposit through, minting rsETH beyond the intended ceiling. This breaks the core invariant `getTotalAssetDeposits(ETH) ≤ depositLimitByAsset(ETH)`. No funds are directly stolen and no yield is diverted; the protocol simply accepts more ETH than its configured limit, which constitutes a failure to deliver the promised deposit cap guarantee.

## Likelihood Explanation
**Medium.** The condition `totalAssetDeposits == depositLimit` is a natural state the protocol reaches whenever the cap is fully subscribed. Any unprivileged depositor monitoring on-chain state can call `depositETH` at that moment. No special role, flash loan, oracle manipulation, or front-running is required — only a standard ETH deposit call with any nonzero `msg.value` above `minAmountToDeposit`.

## Recommendation
Apply the same `+ amount` term to the ETH branch:

```diff
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
-       return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
+       return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

## Proof of Concept
1. Admin sets `depositLimitByAsset[ETH_TOKEN] = 1000 ether` via `LRTConfig.updateAssetDepositLimit`.
2. Depositors fill the pool until `getTotalAssetDeposits(ETH_TOKEN) == 1000 ether`.
3. `getAssetCurrentLimit(ETH_TOKEN)` returns `0`, correctly signalling no remaining capacity.
4. Any caller invokes `depositETH{value: 1 ether}(0, "")`.
5. Inside `_checkIfDepositAmountExceedesCurrentLimit`: `1000 ether > 1000 ether` → `false` → no revert.
6. `_mintRsETH` mints rsETH; `getTotalAssetDeposits(ETH_TOKEN)` becomes `1001 ether`, exceeding the cap.

**Foundry invariant test plan:**
```solidity
function invariant_ethDepositNeverExceedsLimit() public {
    uint256 total = depositPool.getTotalAssetDeposits(ETH_TOKEN);
    uint256 limit = lrtConfig.depositLimitByAsset(ETH_TOKEN);
    assertLe(total, limit);
}
```
A fuzzer driving `depositETH` calls will break this invariant at the boundary condition, confirming the bug.