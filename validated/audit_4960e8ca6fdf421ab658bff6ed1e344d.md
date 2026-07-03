### Title
Conditional Oracle Zero-Address Guard in `updatePriceOracleFor` Enables Blocking of `updateRSETHPrice()` and Withdrawal Queue — (File: contracts/LRTOracle.sol)

### Summary
`LRTOracle.updatePriceOracleFor` only enforces a non-zero oracle address when the asset is **currently** in the supported list. If an asset is removed from the supported list and its oracle is then set to `address(0)`, re-adding the asset to the supported list leaves `assetPriceOracle[asset] == address(0)`. Every subsequent call to `updateRSETHPrice()` reverts because `_getTotalEthInProtocol()` iterates over all supported assets and calls `getAssetPrice()` for each. `unlockQueue` and `initiateWithdrawal` for the affected asset also revert, permanently freezing the withdrawal queue for that asset until the oracle is manually corrected.

### Finding Description

`LRTOracle.updatePriceOracleFor` contains a conditional guard:

```solidity
// contracts/LRTOracle.sol
function updatePriceOracleFor(address asset, address priceOracle) public onlyLRTAdmin {
    if (lrtConfig.isSupportedAsset(asset)) {
        UtilLib.checkNonZeroAddress(priceOracle);   // ← only checked when asset is supported
    }
    assetPriceOracle[asset] = priceOracle;           // ← address(0) accepted for non-supported assets
    emit AssetPriceOracleUpdate(asset, priceOracle);
}
``` [1](#0-0) 

When `lrtConfig.isSupportedAsset(asset)` is `false`, the zero-address check is skipped entirely, so `assetPriceOracle[asset] = address(0)` is written without revert.

`LRTConfig.removeSupportedAsset` deletes `isSupportedAsset[asset]` but does **not** touch `LRTOracle.assetPriceOracle`:

```solidity
// contracts/LRTConfig.sol
delete isSupportedAsset[asset];
delete assetStrategy[asset];
depositLimitByAsset[asset] = 0;
``` [2](#0-1) 

After removal, the admin can call `updatePriceOracleFor(asset, address(0))` without revert. If the asset is later re-added via `addNewSupportedAsset`, it re-enters `supportedAssetList` with `assetPriceOracle[asset] == address(0)`.

`_getTotalEthInProtocol()` iterates over every entry in `supportedAssetList` and calls `getAssetPrice()` for each:

```solidity
// contracts/LRTOracle.sol
address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
    address asset = supportedAssets[assetIdx];
    uint256 assetER = getAssetPrice(asset);   // ← reverts if oracle == address(0)
    ...
}
``` [3](#0-2) 

`getAssetPrice` is guarded by `onlySupportedOracle`:

```solidity
modifier onlySupportedOracle(address asset) {
    if (assetPriceOracle[asset] == address(0)) {
        revert AssetOracleNotSupported();
    }
    _;
}
``` [4](#0-3) 

So `updateRSETHPrice()` → `_updateRsETHPrice()` → `_getTotalEthInProtocol()` → `getAssetPrice(affectedAsset)` reverts, blocking the rsETH price update for the **entire protocol**.

`LRTWithdrawalManager.unlockQueue` calls `_createUnlockParams`, which calls `lrtOracle.getAssetPrice(asset)`:

```solidity
// contracts/LRTWithdrawalManager.sol
return UnlockParams({
    rsETHPrice: lrtOracle.rsETHPrice(),
    assetPrice: lrtOracle.getAssetPrice(asset),   // ← reverts for affected asset
    totalAvailableAssets: unstakingVault.balanceOf(asset)
});
``` [5](#0-4) 

`initiateWithdrawal` calls `getExpectedAssetAmount`, which also calls `lrtOracle.getAssetPrice(asset)`:

```solidity
// contracts/LRTWithdrawalManager.sol
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
``` [6](#0-5) 

### Impact Explanation

**Medium — Temporary freezing of funds.**

Once the affected asset is re-added to the supported list with `assetPriceOracle == address(0)`:

1. `updateRSETHPrice()` is blocked for the **entire protocol** (all assets), because `_getTotalEthInProtocol` iterates over all supported assets. The rsETH price becomes permanently stale until the oracle is corrected.
2. `unlockQueue` for the affected asset reverts, freezing all pending withdrawal requests for that asset. Users who have already transferred rsETH into the withdrawal manager cannot have their requests processed.
3. `initiateWithdrawal` for the affected asset reverts, blocking new withdrawals.

The freeze persists until the admin calls `updatePriceOracleFor(asset, validOracle)` to correct the mapping.

### Likelihood Explanation

**Low.** The scenario requires three sequential admin actions:
1. `LRTConfig.removeSupportedAsset(asset, index)` — requires `getTotalAssetDeposits(asset) <= maxNegligibleAmount`.
2. `LRTOracle.updatePriceOracleFor(asset, address(0))` — succeeds because the asset is no longer supported.
3. `LRTConfig.addNewSupportedAsset(asset, depositLimit)` — re-adds the asset with a zeroed oracle.

This could occur during a legitimate asset migration or oracle upgrade where the admin clears the old oracle entry before re-adding the asset, not realizing the conditional guard permits `address(0)` for non-supported assets.

### Recommendation

Remove the conditional guard and always enforce a non-zero oracle address:

```solidity
function updatePriceOracleFor(address asset, address priceOracle) public onlyLRTAdmin {
    UtilLib.checkNonZeroAddress(priceOracle);
    assetPriceOracle[asset] = priceOracle;
    emit AssetPriceOracleUpdate(asset, priceOracle);
}
```

If clearing the oracle for a removed asset is intentional, introduce a separate `clearOracleForRemovedAsset(address asset)` function that explicitly verifies the asset is not supported before allowing the zero-address write, and ensures the asset cannot be re-added without first setting a valid oracle.

### Proof of Concept

```
1. Asset X is supported; assetPriceOracle[X] = validOracle.
   Users initiate withdrawals for X; rsETH is locked in LRTWithdrawalManager.

2. Admin: LRTConfig.removeSupportedAsset(X, idx)
   → isSupportedAsset[X] = false
   → assetPriceOracle[X] still = validOracle (LRTOracle not touched)

3. Admin: LRTOracle.updatePriceOracleFor(X, address(0))
   → lrtConfig.isSupportedAsset(X) == false → zero-address check skipped
   → assetPriceOracle[X] = address(0)  ✓ no revert

4. Admin: LRTConfig.addNewSupportedAsset(X, depositLimit)
   → isSupportedAsset[X] = true
   → X is back in supportedAssetList
   → assetPriceOracle[X] still == address(0)

5. LRTOracle.updateRSETHPrice()
   → _updateRsETHPrice() → _getTotalEthInProtocol()
   → iterates supportedAssetList, hits X
   → getAssetPrice(X) → onlySupportedOracle reverts: AssetOracleNotSupported
   ✗ updateRSETHPrice blocked for entire protocol

6. LRTWithdrawalManager.unlockQueue(X, ...)
   → _createUnlockParams → lrtOracle.getAssetPrice(X) → reverts
   ✗ withdrawal queue for X permanently frozen

7. LRTWithdrawalManager.initiateWithdrawal(X, ...)
   → getExpectedAssetAmount(X, ...) → lrtOracle.getAssetPrice(X) → reverts
   ✗ new withdrawals for X blocked
```

### Citations

**File:** contracts/LRTOracle.sol (L40-45)
```text
    modifier onlySupportedOracle(address asset) {
        if (assetPriceOracle[asset] == address(0)) {
            revert AssetOracleNotSupported();
        }
        _;
    }
```

**File:** contracts/LRTOracle.sol (L113-119)
```text
    function updatePriceOracleFor(address asset, address priceOracle) public onlyLRTAdmin {
        if (lrtConfig.isSupportedAsset(asset)) {
            UtilLib.checkNonZeroAddress(priceOracle);
        }
        assetPriceOracle[asset] = priceOracle;
        emit AssetPriceOracleUpdate(asset, priceOracle);
    }
```

**File:** contracts/LRTOracle.sol (L333-348)
```text
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

            unchecked {
                ++assetIdx;
            }
        }
```

**File:** contracts/LRTConfig.sol (L86-88)
```text
        delete isSupportedAsset[asset];
        delete assetStrategy[asset];
        depositLimitByAsset[asset] = 0;
```

**File:** contracts/LRTWithdrawalManager.sol (L593-593)
```text
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

**File:** contracts/LRTWithdrawalManager.sol (L846-850)
```text
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
```
