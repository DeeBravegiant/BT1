### Title
`depositLimitByAsset` can be reduced below current total deposits, freezing all new deposits - (File: contracts/LRTConfig.sol)

### Summary
`updateAssetDepositLimit` in `LRTConfig.sol` allows the MANAGER role to set `depositLimitByAsset[asset]` to any value — including a value below the current total deposits — with no validation against the live protocol state. This causes `_checkIfDepositAmountExceedesCurrentLimit` in `LRTDepositPool.sol` to permanently return `true`, blocking all new deposits for the affected asset.

### Finding Description
`updateAssetDepositLimit` (LRTConfig.sol:123–133) unconditionally overwrites `depositLimitByAsset[asset]` with the caller-supplied value:

```solidity
function updateAssetDepositLimit(address asset, uint256 depositLimit)
    external
    onlyRole(LRTConstants.MANAGER)
    onlySupportedAsset(asset)
{
    depositLimitByAsset[asset] = depositLimit;          // no lower-bound check
    emit AssetDepositLimitUpdate(asset, depositLimit);
}
``` [1](#0-0) 

There is no check that `depositLimit >= getTotalAssetDeposits(asset)`. This is inconsistent with `_addNewSupportedAsset`, which does enforce `depositLimit != 0`: [2](#0-1) 

Once `depositLimitByAsset[asset]` is set below the current total deposits, `_checkIfDepositAmountExceedesCurrentLimit` in `LRTDepositPool.sol` evaluates to `true` for every subsequent deposit call:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount)
    internal view returns (bool)
{
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
``` [3](#0-2) 

This causes `_beforeDeposit` to revert with `MaximumDepositLimitReached` on every deposit attempt: [4](#0-3) 

### Impact Explanation
All new deposits for the affected asset are frozen. Any depositor calling `depositAsset` or `depositETH` on `LRTDepositPool` will revert. The freeze persists until the MANAGER raises the limit again. This constitutes a **temporary freezing of funds** (medium impact) — users cannot enter the protocol for the affected asset.

### Likelihood Explanation
The MANAGER role is a privileged but operationally active role (it is not a timelock-gated admin). A misconfiguration — e.g., setting the limit to a nominal value without checking current deposits — is a realistic operational error. No malicious intent is required; the missing validation makes the error silent and easy to commit.

### Recommendation
Add a lower-bound check in `updateAssetDepositLimit` to ensure the new limit is not below the current total deposits:

```solidity
function updateAssetDepositLimit(address asset, uint256 depositLimit)
    external
    onlyRole(LRTConstants.MANAGER)
    onlySupportedAsset(asset)
{
    address depositPool = getContract(LRTConstants.LRT_DEPOSIT_POOL);
    if (depositLimit < ILRTDepositPool(depositPool).getTotalAssetDeposits(asset)) {
        revert InvalidDepositLimit();
    }
    depositLimitByAsset[asset] = depositLimit;
    emit AssetDepositLimitUpdate(asset, depositLimit);
}
```

### Proof of Concept
1. Current `getTotalAssetDeposits(stETH)` = 50,000 stETH.
2. MANAGER calls `updateAssetDepositLimit(stETH, 10_000 ether)`.
3. `depositLimitByAsset[stETH]` is now 10,000 stETH.
4. Any user calls `depositAsset(stETH, 1 ether, ...)`.
5. `_checkIfDepositAmountExceedesCurrentLimit` evaluates `50_000e18 + 1e18 > 10_000e18` → `true`.
6. `_beforeDeposit` reverts with `MaximumDepositLimitReached`.
7. All new stETH deposits are frozen until the MANAGER corrects the limit.

### Citations

**File:** contracts/LRTConfig.sol (L106-118)
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
    }
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
