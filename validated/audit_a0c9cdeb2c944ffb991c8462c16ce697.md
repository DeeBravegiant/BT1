Audit Report

## Title
ETH Deposit Limit Bypass at Boundary Condition Due to Missing `amount` in Limit Check - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` uses an asymmetric comparison for ETH versus ERC20 assets: the ETH branch evaluates `totalAssetDeposits > limit` (omitting `amount`), while the ERC20 branch correctly evaluates `totalAssetDeposits + amount > limit`. When `totalAssetDeposits == depositLimit`, the ETH branch returns `false`, allowing the deposit to proceed and pushing total ETH deposits beyond the configured cap.

## Finding Description
In `contracts/LRTDepositPool.sol` at L676–682, the function `_checkIfDepositAmountExceedesCurrentLimit` contains two branches:

```solidity
// L678-681
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // missing `amount`
}
return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // correct
``` [1](#0-0) 

The ETH branch never adds `amount` to `totalAssetDeposits` before comparing against the limit. When `totalAssetDeposits == depositLimit`, the expression `totalAssetDeposits > limit` evaluates to `false`, so `_beforeDeposit` (L661–663) does not revert with `MaximumDepositLimitReached`, and `_mintRsETH` proceeds to mint rsETH for the depositor. [2](#0-1) 

## Impact Explanation
The ETH deposit cap configured by the admin via `depositLimitByAsset` is not enforced at the exact boundary. Any depositor can push ETH deposits beyond the protocol's intended ceiling. No funds are stolen or frozen, but the protocol fails to deliver its promised deposit-limit guarantee for ETH.

**Impact: Low — Contract fails to deliver promised returns.**

## Likelihood Explanation
Any unprivileged depositor can call `depositETH` at any time. The boundary condition `totalAssetDeposits == depositLimit` is reachable in normal operation as the pool fills up. No special privileges, front-running, or external compromise is required; the condition is naturally hit and the bypass is repeatable on every subsequent ETH deposit until the limit is corrected.

**Likelihood: Medium.**

## Recommendation
Include `amount` in the ETH branch, consistent with the ERC20 branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

## Proof of Concept
1. Admin sets `depositLimitByAsset(ETH_TOKEN) = 100 ether`.
2. Protocol accumulates exactly `100 ether` in ETH deposits (`totalAssetDeposits == 100 ether`).
3. Alice calls `depositETH{value: 1 ether}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit` evaluates `100 ether > 100 ether` → `false` → no revert.
5. `_mintRsETH` mints rsETH for Alice; `totalAssetDeposits` becomes `101 ether`, exceeding the limit.
6. For comparison, an equivalent ERC20 deposit would evaluate `100 ether + 1 ether > 100 ether` → `true` → revert `MaximumDepositLimitReached`.

**Foundry test plan:** Deploy `LRTDepositPool` on a local fork, set `depositLimitByAsset(ETH_TOKEN) = 100 ether`, simulate deposits totaling exactly `100 ether`, then call `depositETH{value: 1 ether}` and assert it does not revert. Confirm `getTotalAssetDeposits(ETH_TOKEN)` returns `101 ether`, exceeding the configured limit.

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
