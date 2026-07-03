### Title
Stale `highestRsethPrice` and `assetPriceOracle` Not Cleared on Asset Removal Causes Incorrect rsETH Price and Potential Protocol Freeze - (File: `contracts/LRTOracle.sol`)

---

### Summary

When a supported asset is removed from `LRTConfig` via `removeSupportedAsset()`, two critical oracle state variables in `LRTOracle` are **not cleared**: `highestRsethPrice` and `assetPriceOracle[asset]`. This mirrors the HydraDX M-07 pattern exactly: stale oracle data persists after asset removal, causing incorrect price behavior when the asset is re-added or when the price is next updated. The stale `highestRsethPrice` can cause the public `updateRSETHPrice()` function to incorrectly trigger the downside protection mechanism, pausing `LRTDepositPool` and `LRTWithdrawalManager` and freezing all user deposits and withdrawals.

---

### Finding Description

`LRTConfig.removeSupportedAsset()` removes an asset from the protocol's supported list and clears its deposit limit and strategy mapping. However, it does **not** notify or update `LRTOracle`. Two pieces of stale state remain:

**1. `highestRsethPrice` is not reset.**

`highestRsethPrice` is a persistent high-water mark used by `_updateRsETHPrice()` for both upside and downside protection. When an asset is removed, `_getTotalEthInProtocol()` no longer counts that asset's TVL, so the next call to `updateRSETHPrice()` computes a lower `newRsETHPrice`. But `highestRsethPrice` still reflects the old (higher) value that included the removed asset. The downside protection logic then compares the new lower price against the stale peak:

```solidity
// contracts/LRTOracle.sol:270-282
if (newRsETHPrice < highestRsethPrice) {
    uint256 diff = highestRsethPrice - newRsETHPrice;
    bool isPriceDecreaseOffLimit =
        pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

    if (isPriceDecreaseOffLimit) {
        if (!lrtDepositPool.paused()) lrtDepositPool.pause();
        if (!withdrawalManager.paused()) withdrawalManager.pause();
        _pause();
        return;
    }
```

If the removed asset contributed enough TVL that the resulting price drop exceeds `pricePercentageLimit`, the protocol is paused — freezing all deposits and withdrawals.

**2. `assetPriceOracle[asset]` is not cleared.**

`removeSupportedAsset()` does not call `updatePriceOracleFor(asset, address(0))`. The old oracle address remains in `LRTOracle.assetPriceOracle[asset]`. When the asset is re-added via `addNewSupportedAsset()`, `_getTotalEthInProtocol()` immediately uses the stale oracle without requiring any admin action:

```solidity
// contracts/LRTOracle.sol:336-343
for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
    address asset = supportedAssets[assetIdx];
    uint256 assetER = getAssetPrice(asset);   // uses assetPriceOracle[asset] — still old oracle
    uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
    totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

If the re-added asset's market price has changed and the admin intended to deploy a new oracle, the old oracle silently produces a wrong exchange rate, causing `rsETHPrice` to be miscalculated and users to receive incorrect rsETH amounts.

---

### Impact Explanation

**Primary impact — Temporary freezing of funds (Medium):**
Any user can call the public `updateRSETHPrice()` after an asset is removed. If the TVL drop from removal causes `newRsETHPrice` to fall more than `pricePercentageLimit` below the stale `highestRsethPrice`, the function pauses `LRTDepositPool` and `LRTWithdrawalManager`, blocking all deposits and withdrawals for all users until an admin manually unpauses.

**Secondary impact — Incorrect rsETH price / value leak (Low):**
When the asset is re-added, the stale `assetPriceOracle` is used silently. If the oracle returns a price different from the asset's current market rate, `rsETHPrice` is wrong. Depositors receive too many or too few rsETH tokens relative to the true protocol NAV, constituting a value transfer between depositors and existing rsETH holders.

---

### Likelihood Explanation

Asset removal is a legitimate, documented admin operation in `LRTConfig`. The `pricePercentageLimit` is a live protocol parameter. If a non-trivial asset (e.e., stETH or ETHx) is removed, the TVL drop will be large enough to exceed any reasonable `pricePercentageLimit`. The trigger for the freeze — `updateRSETHPrice()` — is a public, permissionless function callable by any address. No attacker capability beyond calling a public function is required after the admin's legitimate removal action.

---

### Recommendation

1. In `LRTConfig.removeSupportedAsset()`, call `ILRTOracle(oracle).updatePriceOracleFor(asset, address(0))` to clear the stale oracle entry.
2. Add an admin-callable `resetHighestRsethPrice()` function in `LRTOracle` (restricted to `onlyLRTAdmin`) that resets `highestRsethPrice` to the current `rsETHPrice`, to be called as part of the asset-removal workflow before `updateRSETHPrice()` is invoked publicly.
3. Alternatively, `removeSupportedAsset()` should atomically call `updateRSETHPrice()` internally (with the new, lower TVL) and then reset `highestRsethPrice` to the resulting price, so the high-water mark reflects the post-removal state.

---

### Proof of Concept

1. Protocol has two assets: stETH and ETHx. rsETH price = 1.10 ETH. `highestRsethPrice = 1.10e18`. `pricePercentageLimit = 5e16` (5%).
2. Admin calls `LRTConfig.removeSupportedAsset(stETH, 0)`. stETH is removed from `supportedAssetList`. `LRTOracle.highestRsethPrice` remains `1.10e18`. `LRTOracle.assetPriceOracle[stETH]` remains set to the old Chainlink oracle.
3. `_getTotalEthInProtocol()` now only counts ETHx. Suppose the true new rsETH price is `1.00e18` (a 9.09% drop from `highestRsethPrice`).
4. Any user calls `LRTOracle.updateRSETHPrice()` (public, no access control).
5. Inside `_updateRsETHPrice()`: `newRsETHPrice = 1.00e18`, `highestRsethPrice = 1.10e18`, `diff = 0.10e18`, `pricePercentageLimit.mulWad(highestRsethPrice) = 0.055e18`. Since `0.10e18 > 0.055e18`, `isPriceDecreaseOffLimit = true`.
6. `LRTDepositPool.pause()`, `LRTWithdrawalManager.pause()`, and `LRTOracle._pause()` are all called. All user deposits and withdrawals are frozen.
7. Later, admin re-adds stETH via `addNewSupportedAsset(stETH, limit)`. The old `assetPriceOracle[stETH]` is still set. If the admin does not explicitly call `updatePriceOracleFor(stETH, newOracle)`, the old oracle is used silently in all subsequent `_getTotalEthInProtocol()` calls, producing a wrong rsETH price.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/LRTOracle.sol (L28-30)
```text
    uint256 public override rsETHPrice;
    uint256 public pricePercentageLimit;
    uint256 public highestRsethPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L270-282)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }
```

**File:** contracts/LRTOracle.sol (L292-296)
```text

        // update highest price if new price exceeds it
        if (newRsETHPrice > highestRsethPrice) {
            highestRsethPrice = newRsETHPrice;
        }
```

**File:** contracts/LRTOracle.sol (L331-349)
```text
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
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
    }
```

**File:** contracts/LRTConfig.sol (L66-94)
```text
    function removeSupportedAsset(
        address asset,
        uint256 tokenIndex
    )
        external
        onlySupportedAsset(asset)
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        UtilLib.checkNonZeroAddress(asset);

        if (supportedAssetList[tokenIndex] != asset) {
            revert TokenNotFoundError();
        }

        address depositPool = getContract(LRTConstants.LRT_DEPOSIT_POOL);

        if (ILRTDepositPool(depositPool).getTotalAssetDeposits(asset) > maxNegligibleAmount) {
            revert CannotRemoveAssetWithDeposits(asset);
        }

        delete isSupportedAsset[asset];
        delete assetStrategy[asset];
        depositLimitByAsset[asset] = 0;

        supportedAssetList[tokenIndex] = supportedAssetList[supportedAssetList.length - 1];
        supportedAssetList.pop();

        emit RemovedSupportedAsset(asset);
    }
```
