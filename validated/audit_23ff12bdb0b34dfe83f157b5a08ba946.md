Audit Report

## Title
ETH Deposit Limit Bypass Due to Missing `amount` in Cap Check - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` omits the incoming `amount` from the ETH branch comparison, checking only whether the current total already exceeds the cap rather than whether the new deposit would exceed it. Any depositor can bypass the configured ETH deposit cap in a single transaction, violating the protocol's risk-management invariant.

## Finding Description
In `_checkIfDepositAmountExceedesCurrentLimit` (L676–682), the ETH branch evaluates `totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)` without adding `amount`, while the ERC-20 branch correctly evaluates `totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)`. [1](#0-0) 

`depositETH` passes `msg.value` as `depositAmount` to `_beforeDeposit`, which forwards it to this check. [2](#0-1)  `_beforeDeposit` reverts only when `_checkIfDepositAmountExceedesCurrentLimit` returns `true`. [3](#0-2) 

Because the ETH branch never adds `amount`, the check returns `false` (not exceeded) for any deposit size as long as `totalAssetDeposits ≤ depositLimit` at call time. The full `msg.value` is then minted as rsETH with no cap enforcement. [4](#0-3) 

## Impact Explanation
The deposit cap is a protocol-enforced risk-management boundary. Bypassing it allows the pool to silently accumulate ETH far beyond the operator-configured limit. No funds are directly stolen or frozen, but the contract fails to deliver its promised deposit-cap guarantee. This matches the allowed impact: **Low — Contract fails to deliver promised returns, but doesn't lose value.** [5](#0-4) 

## Likelihood Explanation
Any unprivileged external caller can trigger this by calling `depositETH` with a large `msg.value` while `getTotalAssetDeposits(ETH_TOKEN) ≤ depositLimit`. No special role, coordination, or precondition beyond a non-full pool is required. The condition is routinely met during normal protocol operation. [6](#0-5) 

## Recommendation
Add `amount` to the ETH branch to mirror the ERC-20 path:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
``` [5](#0-4) 

## Proof of Concept
1. Admin sets `depositLimitByAsset[ETH_TOKEN] = 1000 ether`.
2. `getTotalAssetDeposits(ETH_TOKEN)` returns `999 ether`.
3. Attacker calls `depositETH{value: 5000 ether}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit` evaluates `999 ether > 1000 ether` → `false` → no revert.
5. `_mintRsETH` mints rsETH for 5000 ETH; pool now holds 5999 ETH against a 1000 ETH cap.

Foundry test plan: deploy `LRTDepositPool` with a mock `lrtConfig` returning `depositLimitByAsset = 1000 ether`, seed the pool with 999 ETH of prior deposits, call `depositETH{value: 5000 ether}`, assert `getTotalAssetDeposits(ETH_TOKEN) == 5999 ether` and that no revert occurred. [1](#0-0)

### Citations

**File:** contracts/LRTDepositPool.sol (L80-93)
```text
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```

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
