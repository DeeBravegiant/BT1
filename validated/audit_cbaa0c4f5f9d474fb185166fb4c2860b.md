The code at lines 676–682 of `contracts/LRTDepositPool.sol` confirms the claim exactly. The ETH branch omits `amount` from the comparison, while the ERC20 branch correctly includes it.

Audit Report

## Title
ETH Deposit Limit Check Omits Incoming Amount, Allowing Deposit Cap Bypass — (File: `contracts/LRTDepositPool.sol`)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` uses an asymmetric check: for ETH it evaluates `totalAssetDeposits > depositLimit` (ignoring the incoming `amount`), while for ERC20 assets it correctly evaluates `totalAssetDeposits + amount > depositLimit`. As a result, any depositor can push ETH deposits beyond the configured cap in a single transaction, causing excess ETH to sit idle in the pool and diluting rsETH yield for all holders.

## Finding Description
In `contracts/LRTDepositPool.sol` at lines 676–682:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)); // amount omitted
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // amount included
}
``` [1](#0-0) 

The ETH branch only fires when the limit is **already** exceeded before the deposit arrives. The public `depositETH` function (lines 76–93) calls `_beforeDeposit` → `_checkIfDepositAmountExceedesCurrentLimit`, and reverts with `MaximumDepositLimitReached` only when the check returns `true`. [2](#0-1) 

Because `amount` is never added in the ETH branch, a depositor whose transaction would push the total from `limit - 1 wei` to `limit + N ETH` passes the check and the full deposit is accepted. No existing guard compensates for this omission.

## Impact Explanation
The deposit limit is the protocol's primary mechanism for capping ETH exposure per asset. Bypassing it allows more ETH to be deposited than the protocol intends to deploy into EigenLayer strategies. Excess ETH that cannot be deployed sits idle in `LRTDepositPool`, earning no yield, diluting the rsETH exchange rate for all existing holders. No principal is lost, but the protocol fails to enforce its own promised cap.

**Impact: Low** — Contract fails to deliver promised returns (deposit limit not enforced for ETH), but no direct loss of value.

## Likelihood Explanation
The entry point is the public, permissionless `depositETH` function. No special role or precondition is required beyond having ETH to deposit. The vulnerable condition (`totalAssetDeposits(ETH) ≤ depositLimit`) is the normal operating state of the protocol. Any depositor can trigger this at any time.

**Likelihood: High.**

## Recommendation
Add `amount` to the ETH branch to match the ERC20 branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

## Proof of Concept
1. Admin sets `depositLimitByAsset(ETH) = 1000 ether`.
2. Current `getTotalAssetDeposits(ETH) = 999 ether`.
3. Depositor calls `depositETH{value: 500 ether}(...)`.
4. `_checkIfDepositAmountExceedesCurrentLimit(ETH, 500 ether)` evaluates `999 ether > 1000 ether` → `false`.
5. Deposit is accepted; total ETH in protocol becomes `1499 ether` — 499 ETH over the intended cap.
6. The 499 ETH excess sits idle in `LRTDepositPool`, not deployed to EigenLayer, earning no yield, diluting rsETH price for all holders.

**Foundry test plan:** Deploy `LRTDepositPool` with `depositLimitByAsset(ETH) = 1000 ether`, seed `999 ether` of prior deposits, call `depositETH{value: 500 ether}`, assert the call succeeds and `getTotalAssetDeposits(ETH) == 1499 ether`, confirming the cap was bypassed.

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
