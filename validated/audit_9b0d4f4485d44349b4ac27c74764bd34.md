### Title
`assetsCommitted` Not Reduced in `unlockQueue()` Causes `getAvailableAssetAmount` to Be Artificially Deflated — (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

After `unlockQueue()` moves assets from the `LRTUnstakingVault` into the `LRTWithdrawalManager` contract, `getTotalAssetDeposits()` decreases (those assets are no longer tracked), but `assetsCommitted[asset]` is not reduced. The available-asset check used in `initiateWithdrawal()` therefore under-reports capacity, temporarily blocking new withdrawal requests even when the protocol is well-capitalised.

---

### Finding Description

The vulnerability class is **share/asset mis-accounting**: a capacity metric is computed from two variables that fall out of sync when assets transition between internal states.

**Step 1 — `initiateWithdrawal()` commits assets** [1](#0-0) 

When a user initiates a withdrawal, rsETH is transferred to the withdrawal manager (not burned) and `assetsCommitted[asset]` is incremented by `expectedAssetAmount`. The underlying assets remain in the unstaking vault and are still counted by `getTotalAssetDeposits()`.

**Step 2 — `getAvailableAssetAmount()` is the gating check** [2](#0-1) 

```
availableAssetAmount = totalAssets > assetsCommitted[asset]
                       ? totalAssets - assetsCommitted[asset]
                       : 0;
```

`totalAssets` is sourced from `getTotalAssetDeposits()`, which sums assets across the deposit pool, NDCs, EigenLayer, converter, and **unstaking vault**. [3](#0-2) 

Crucially, assets sitting inside `LRTWithdrawalManager` itself are **not** included in any of those buckets.

**Step 3 — `unlockQueue()` moves assets but does not reduce `assetsCommitted`** [4](#0-3) 

`unlockQueue()` burns rsETH and calls `unstakingVault.redeem(asset, assetAmountUnlocked)`, pulling assets from the vault into the withdrawal manager. After this call:

| Variable | Change |
|---|---|
| `unstakingVault.balanceOf(asset)` | **decreases** by `assetAmountUnlocked` |
| `getTotalAssetDeposits(asset)` | **decreases** by `assetAmountUnlocked` |
| `assetsCommitted[asset]` | **unchanged** (not decremented here) |

The result:

```
getAvailableAssetAmount()
  = (totalAssets − assetAmountUnlocked) − assetsCommitted
  = (prior available) − assetAmountUnlocked
```

The unlocked amount is effectively **subtracted twice**: once because it left `getTotalAssetDeposits()`, and once because it is still counted in `assetsCommitted`. This is the direct analog of M-4, where `lockCapital()` reduces the numerator (`totalSTokenUnderlying`) without reducing the denominator (`totalProtections`).

**Step 4 — New withdrawal requests are blocked** [5](#0-4) 

```solidity
if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
```

Any new `initiateWithdrawal()` call that would have succeeded before `unlockQueue()` may now revert, even though the protocol holds sufficient assets in other locations (deposit pool, NDCs, EigenLayer).

---

### Impact Explanation

**Temporary freezing of funds — Medium.**

Between the `unlockQueue()` call and the completion of all pending `completeWithdrawal()` calls, `getAvailableAssetAmount()` is understated by the full `assetAmountUnlocked`. During this window, users who attempt to initiate new withdrawal requests receive `ExceedAmountToWithdraw` reverts. The freeze lasts until `assetsCommitted` is decremented (presumably inside `_processWithdrawalCompletion()`). Given the `withdrawalDelayBlocks` default of 8 days, this window can be substantial.

---

### Likelihood Explanation

`unlockQueue()` is a routine operational call made by the `ASSET_TRANSFER_ROLE` or `OPERATOR_ROLE` to process the withdrawal queue. It is called regularly as part of normal protocol operation. Every invocation that processes a non-zero queue triggers the accounting divergence. No special attacker action is required; any user attempting `initiateWithdrawal()` in the affected window is impacted.

---

### Recommendation

Decrement `assetsCommitted[asset]` inside `unlockQueue()` (or inside `_unlockWithdrawalRequests()`) by `assetAmountUnlocked` at the point where assets leave the unstaking vault. This mirrors the M-4 fix: do not count assets that have already been moved out of the tracked pool when computing the available capacity.

```solidity
// inside unlockQueue(), after unstakingVault.redeem(asset, assetAmountUnlocked):
if (assetsCommitted[asset] >= assetAmountUnlocked) {
    assetsCommitted[asset] -= assetAmountUnlocked;
} else {
    assetsCommitted[asset] = 0;
}
```

---

### Proof of Concept

1. Protocol state: 1 000 ETH in unstaking vault, `assetsCommitted[ETH] = 800 ETH`, `getAvailableAssetAmount() = 200 ETH`.
2. Operator calls `unlockQueue(ETH, ...)` → 800 ETH redeemed from vault into withdrawal manager; rsETH burned.
3. Now: `getTotalAssetDeposits(ETH)` = 200 ETH (vault emptied of the 800 ETH), `assetsCommitted[ETH]` = 800 ETH (unchanged).
4. `getAvailableAssetAmount()` = max(0, 200 − 800) = **0 ETH**.
5. Any user calling `initiateWithdrawal(ETH, amount)` reverts with `ExceedAmountToWithdraw`, even though the protocol still holds 200 ETH in the deposit pool and the 800 ETH is sitting in the withdrawal manager ready for distribution.
6. The freeze persists until all 800 ETH worth of `completeWithdrawal()` calls are processed and `assetsCommitted` is decremented back to 0. [6](#0-5) [7](#0-6) [3](#0-2)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-173)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;
```

**File:** contracts/LRTWithdrawalManager.sol (L268-320)
```text
    function unlockQueue(
        address asset,
        uint256 firstExcludedIndex,
        uint256 minimumAssetPrice,
        uint256 minimumRsEthPrice,
        uint256 maximumAssetPrice,
        uint256 maximumRsEthPrice
    )
        external
        nonReentrant
        onlySupportedAsset(asset)
        whenNotPaused
        onlyAssetTransferOrOperatorRole
        returns (uint256 rsETHBurned, uint256 assetAmountUnlocked)
    {
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));

        UnlockParams memory params = _createUnlockParams(lrtOracle, unstakingVault, asset);

        _validatePrices(
            params.rsETHPrice,
            params.assetPrice,
            minimumRsEthPrice,
            maximumRsEthPrice,
            minimumAssetPrice,
            maximumAssetPrice
        );

        if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero();

        // Updates and unlocks withdrawal requests up to a specified upper limit or until allocated assets are fully
        // utilized.
        (rsETHBurned, assetAmountUnlocked) = _unlockWithdrawalRequests(
            asset, params.totalAvailableAssets, params.rsETHPrice, params.assetPrice, firstExcludedIndex
        );

        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);

        // If Aave integration is enabled and asset is ETH, deposit to Aave
        if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN && assetAmountUnlocked > 0) {
            try this.depositToAaveExternal(assetAmountUnlocked) { }
            catch (bytes memory reason) {
                emit AaveDepositFailed(assetAmountUnlocked, reason);
                // Silently fail if Aave deposit fails (e.g., pool at max capacity)
                // Funds remain in contract for withdrawals
            }
        }

        emit AssetUnlocked(asset, rsETHBurned, assetAmountUnlocked, params.rsETHPrice, params.assetPrice);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L596-603)
```text
    /// @notice Calculates the amount of asset available for withdrawal.
    /// @param asset The asset address.
    /// @return availableAssetAmount The asset amount avaialble for withdrawal.
    function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
        availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
    }
```

**File:** contracts/LRTDepositPool.sol (L385-397)
```text
    function getTotalAssetDeposits(address asset) public view override returns (uint256 totalAssetDeposit) {
        (
            uint256 assetLyingInDepositPool,
            uint256 assetLyingInNDCs,
            uint256 assetStakedInEigenLayer,
            uint256 assetUnstakingFromEigenLayer,
            uint256 assetLyingInConverter,
            uint256 assetLyingUnstakingVault
        ) = getAssetDistributionData(asset);
        uint256 effectiveAssetWithEigenLayer = assetStakedInEigenLayer + assetUnstakingFromEigenLayer;
        return (assetLyingInDepositPool + assetLyingInNDCs + effectiveAssetWithEigenLayer + assetLyingInConverter
                + assetLyingUnstakingVault);
    }
```
