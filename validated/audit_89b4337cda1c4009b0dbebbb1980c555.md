The analog exists. In `LRTConfig.sol`, `_addNewSupportedAsset` enforces `depositLimit > 0`, but `updateAssetDepositLimit` has no lower-bound check and allows `depositLimit = 0`. This is the exact same inconsistency pattern as the reported finding.

---

### Title
Inconsistent Lower Bound on Deposit Limit Allows Zero Value Update, Freezing Asset Deposits - (File: contracts/LRTConfig.sol)

### Summary
`_addNewSupportedAsset` enforces a non-zero deposit limit when an asset is first whitelisted, but `updateAssetDepositLimit` imposes no lower bound, allowing the MANAGER to set the deposit limit to `0`. This would immediately freeze all new deposits for that asset.

### Finding Description
When a new supported asset is added via `_addNewSupportedAsset`, the function enforces `depositLimit != 0`:

```solidity
// contracts/LRTConfig.sol L108-110
if (depositLimit == 0) {
    revert InvalidDepositLimit();
}
``` [1](#0-0) 

However, the update path `updateAssetDepositLimit` applies no lower-bound check at all:

```solidity
// contracts/LRTConfig.sol L123-133
function updateAssetDepositLimit(
    address asset,
    uint256 depositLimit
) external onlyRole(LRTConstants.MANAGER) onlySupportedAsset(asset) {
    depositLimitByAsset[asset] = depositLimit;
    emit AssetDepositLimitUpdate(asset, depositLimit);
}
``` [2](#0-1) 

### Impact Explanation
When `depositLimitByAsset[asset]` is set to `0`, `_checkIfDepositAmountExceedesCurrentLimit` in `LRTDepositPool` evaluates `totalAssetDeposits + amount > 0`, which is always `true` for any non-zero deposit, causing every call to `depositAsset` or `depositETH` for that asset to revert with `MaximumDepositLimitReached`. [3](#0-2) 

This is a **temporary freezing of funds** (Medium): new deposits are blocked for the affected asset until the limit is corrected, while existing depositors retain their balances and can still withdraw.

### Likelihood Explanation
The MANAGER role is a live operational role that regularly adjusts deposit limits in response to market conditions. The absence of a lower-bound guard on `updateAssetDepositLimit` — while the initial setup function enforces one — creates a silent inconsistency that makes an accidental zero-value update plausible. The original report's scenario maps directly: a manager intending to set a small non-zero limit (e.g., `1 ether`) could accidentally submit `0`.

### Recommendation
Add the same non-zero guard to `updateAssetDepositLimit` that exists in `_addNewSupportedAsset`:

```solidity
function updateAssetDepositLimit(...) external ... {
    if (depositLimit == 0) revert InvalidDepositLimit();
    depositLimitByAsset[asset] = depositLimit;
    emit AssetDepositLimitUpdate(asset, depositLimit);
}
```

### Proof of Concept
1. MANAGER calls `updateAssetDepositLimit(stETH, 0)` — no revert, `depositLimitByAsset[stETH]` becomes `0`.
2. Any user calls `depositAsset(stETH, amount, 0)`.
3. `_checkIfDepositAmountExceedesCurrentLimit` returns `true` (`0 + amount > 0`).
4. Transaction reverts with `MaximumDepositLimitReached`.
5. All stETH deposits are frozen until the MANAGER issues a corrective update. [4](#0-3) [2](#0-1) [3](#0-2)

### Citations

**File:** contracts/LRTConfig.sol (L106-117)
```text
    function _addNewSupportedAsset(address asset, uint256 depositLimit) private {
        UtilLib.checkNonZeroAddress(asset);
        if (depositLimit == 0) {
            revert InvalidDepositLimit();
        }
        if (isSupportedAsset[asset]) {
            revert AssetAlreadySupported();
        }
        isSupportedAsset[asset] = true;
        supportedAssetList.push(asset);
        depositLimitByAsset[asset] = depositLimit;
        emit AddedNewSupportedAsset(asset, depositLimit);
```

**File:** contracts/LRTConfig.sol (L123-133)
```text
    function updateAssetDepositLimit(
        address asset,
        uint256 depositLimit
    )
        external
        onlyRole(LRTConstants.MANAGER)
        onlySupportedAsset(asset)
    {
        depositLimitByAsset[asset] = depositLimit;
        emit AssetDepositLimitUpdate(asset, depositLimit);
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
