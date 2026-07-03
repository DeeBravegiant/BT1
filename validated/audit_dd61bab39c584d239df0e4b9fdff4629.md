Audit Report

## Title
ETH Deposit Limit Check Excludes Deposit Amount, Allowing Bypass of `depositLimitByAsset` - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` applies an asymmetric check: for ETH it evaluates `totalAssetDeposits > limit` (omitting the incoming `amount`), while for LST assets it correctly evaluates `totalAssetDeposits + amount > limit`. Any unprivileged depositor can push total ETH deposits arbitrarily above the configured cap in a single `depositETH` call, bypassing the protocol's primary deposit-cap control.

## Finding Description
The root cause is in `_checkIfDepositAmountExceedesCurrentLimit` at lines 676–682:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // amount excluded
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // amount included
}
``` [1](#0-0) 

The ETH branch only reverts when the cap has **already** been exceeded before the deposit. A deposit that would push the total from just-below-limit to far-above-limit passes the check without error.

The call path is: `depositETH` (line 76, public/payable, guarded only by `whenNotPaused` and `nonReentrant`) → `_beforeDeposit` (line 648) → `_checkIfDepositAmountExceedesCurrentLimit` (line 661) → returns `false` → `_mintRsETH` executes. [2](#0-1) [3](#0-2) 

Neither `nonReentrant` nor `whenNotPaused` prevents this; they are orthogonal guards. No other check compensates for the missing `amount` in the ETH branch.

## Impact Explanation
The `depositLimitByAsset` cap is the protocol's primary risk-management control over ETH exposure. Bypassing it allows unbounded ETH accumulation beyond the admin-configured ceiling. Deposited funds are not lost or stolen, but the contract fails to enforce the deposit cap it promises. This matches the allowed impact: **Low — Contract fails to deliver promised returns, but doesn't lose value.**

## Likelihood Explanation
`depositETH` is public and payable with no access control beyond `whenNotPaused`. Any depositor can trigger this in a single transaction whenever `totalAssetDeposits ≤ depositLimitByAsset(ETH_TOKEN)`. No special role, front-running, or external dependency is required. **Likelihood: High.**

## Recommendation
Include the deposit amount in the ETH branch to match the LST branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

## Proof of Concept
1. Admin sets `depositLimitByAsset[ETH_TOKEN] = 1000 ether`.
2. `getTotalAssetDeposits(ETH_TOKEN)` returns `999 ether`.
3. Attacker calls `depositETH{value: 500 ether}(minRSETH, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit` evaluates `999e18 > 1000e18` → `false` → no revert.
5. `_mintRsETH` mints rsETH for the full 500 ETH.
6. Total ETH deposits become `1499 ether`, exceeding the 1000 ETH cap by 499 ETH.

The equivalent LST call would evaluate `999e18 + 500e18 > 1000e18` → `true` → `MaximumDepositLimitReached` revert, confirming the asymmetry. A Foundry fork test can reproduce this by forking mainnet state, setting the deposit limit, and asserting that `depositETH` does not revert when `msg.value` would breach the cap.

### Citations

**File:** contracts/LRTDepositPool.sol (L76-93)
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
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L648-663)
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
