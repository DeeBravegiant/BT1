Audit Report

## Title
ETH Deposit Limit Bypass via Missing Amount in `_checkIfDepositAmountExceedesCurrentLimit` - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` omits the incoming `amount` from the ETH branch comparison, checking only `totalAssetDeposits > limit` instead of `totalAssetDeposits + amount > limit`. Any unprivileged depositor can therefore exceed the configured `depositLimitByAsset[ETH_TOKEN]` cap in a single `depositETH` call, breaking the protocol's deposit-cap invariant.

## Finding Description
The asymmetry is confirmed at [1](#0-0)  — the ETH branch on line 679 returns `totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)`, while the LST branch on line 681 correctly returns `totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)`. Because `amount` is never added for ETH, the check evaluates to `false` (no limit exceeded) whenever the current total is still below the cap, regardless of how large `msg.value` is. The guard at [2](#0-1)  therefore never reverts for ETH deposits that would push the total far above the limit. The call chain is `depositETH` [3](#0-2)  → `_beforeDeposit` [4](#0-3)  → `_checkIfDepositAmountExceedesCurrentLimit`. No other check in this path enforces the ETH deposit cap.

## Impact Explanation
The `depositLimitByAsset` cap is the protocol's stated risk-management bound per asset. Bypassing it for ETH allows any depositor to push total ETH holdings arbitrarily above the configured limit in one transaction. The contract fails to enforce its promised deposit cap, matching the allowed impact: **Low — contract fails to deliver promised returns**.

## Likelihood Explanation
`depositETH` is `external payable`, `nonReentrant`, `whenNotPaused`, and gated only by `onlySupportedAsset(ETH_TOKEN)` [5](#0-4) . No privileged role, special state, or prior setup is required. Any depositor who sends ETH while `totalAssetDeposits < depositLimitByAsset[ETH_TOKEN]` can exceed the cap in a single call. Likelihood is **High**.

## Recommendation
Apply the same `totalAssetDeposits + amount` pattern to the ETH branch:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

## Proof of Concept
1. Admin sets `depositLimitByAsset[ETH_TOKEN] = 100 ether`.
2. `getTotalAssetDeposits(ETH_TOKEN)` returns `99 ether`.
3. Attacker calls `depositETH{value: 500 ether}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 500 ether)` evaluates `99 ether > 100 ether` → `false` → no revert at [2](#0-1) .
5. `_mintRsETH` mints rsETH for 500 ETH; `getTotalAssetDeposits(ETH_TOKEN)` becomes `599 ether`, nearly 6× the cap.
6. **Foundry test plan**: deploy `LRTDepositPool` on a local fork, set limit to `100 ether`, seed `99 ether` of prior deposits, call `depositETH{value: 500 ether}`, assert `getTotalAssetDeposits(ETH_TOKEN) > depositLimitByAsset[ETH_TOKEN]` and that no revert occurred.

### Citations

**File:** contracts/LRTDepositPool.sol (L80-85)
```text
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
```

**File:** contracts/LRTDepositPool.sol (L87-87)
```text
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
```

**File:** contracts/LRTDepositPool.sol (L648-670)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
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
