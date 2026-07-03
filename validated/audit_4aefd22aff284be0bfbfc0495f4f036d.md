Audit Report

## Title
ETH Deposit Limit Check Omits Incoming Amount, Allowing Deposit Cap Bypass - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` uses `totalAssetDeposits > limit` for ETH but `totalAssetDeposits + amount > limit` for LSTs. The ETH branch never factors in the incoming deposit amount, so any ETH deposit passes the guard as long as the current total does not already exceed the cap. This allows ETH deposits to push the protocol above its configured deposit limit.

## Finding Description
At [1](#0-0)  the function branches on asset type. The ETH path at line 679 evaluates `totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)`, omitting `amount`. The LST path at line 681 correctly evaluates `totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)`.

Consequence: when `totalAssetDeposits == depositLimit`, the ETH expression is `limit > limit` → `false`, so `_beforeDeposit` at [2](#0-1)  does not revert, and the deposit proceeds. The same holds for any state where `totalAssetDeposits < limit` but `totalAssetDeposits + amount > limit`. No other guard exists between `depositETH` and `_mintRsETH`. [3](#0-2) 

## Impact Explanation
The deposit cap is the protocol's primary risk-management invariant for ETH. Bypassing it means the protocol accepts more ETH than governance approved, violating `getTotalAssetDeposits(ETH) ≤ depositLimitByAsset(ETH)`. This maps to **Low – contract fails to deliver promised returns**: the protocol does not enforce its own deposit cap, but no direct fund loss is caused by the bypass alone. Escalation to insolvency requires an independent adverse event (e.g., EigenLayer slashing), which is not proven by the submitted evidence.

## Likelihood Explanation
`depositETH` is public and permissionless. [4](#0-3)  No special role or precondition is required. The vulnerable condition (`totalAssetDeposits` at or near the limit) is a normal operational state. Any depositor can trigger this repeatedly.

## Recommendation
Remove the ETH/LST split and apply the same expression to both asset types:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

## Proof of Concept
1. Governance sets `depositLimitByAsset(ETH_TOKEN) = 1000 ether`.
2. Protocol accumulates `getTotalAssetDeposits(ETH_TOKEN) = 1000 ether` (limit exactly reached).
3. Attacker calls `depositETH{value: 500 ether}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 500e18)` evaluates `1000e18 > 1000e18` → `false` → no revert.
5. `_mintRsETH` executes; total ETH in protocol becomes 1500 ether, 50% above the cap.
6. Foundry invariant test: assert `getTotalAssetDeposits(ETH_TOKEN) <= lrtConfig.depositLimitByAsset(ETH_TOKEN)` after any sequence of `depositETH` calls — this invariant will be broken by the above sequence, confirming the bug. [1](#0-0)

### Citations

**File:** contracts/LRTDepositPool.sol (L76-85)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
```

**File:** contracts/LRTDepositPool.sol (L86-90)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);
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
