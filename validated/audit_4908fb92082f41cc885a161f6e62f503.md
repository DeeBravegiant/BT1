### Title
ERC20 Assets Permanently Frozen in `LRTConverter` After Asset Removal Due to Missing Converter Balance in `getTotalAssetDeposits` - (File: contracts/LRTConverter.sol, contracts/LRTDepositPool.sol, contracts/LRTConfig.sol)

---

### Summary

`LRTDepositPool.getAssetDistributionData` hardcodes `assetLyingInConverter = 0` for ERC20 assets, meaning ERC20 tokens physically held in `LRTConverter` are invisible to `getTotalAssetDeposits`. The `removeSupportedAsset` guard in `LRTConfig` relies exclusively on `getTotalAssetDeposits` to prevent removal of assets with live deposits. Because the converter balance is excluded, the guard can pass while ERC20 tokens remain in `LRTConverter`. Once the asset is removed from the supported list, `LRTConverter.transferAssetToDepositPool` — the only non-unstaking egress path — reverts on its `onlySupportedERC20Token` modifier, permanently freezing those tokens.

---

### Finding Description

**Step 1 — ERC20 assets enter `LRTConverter`.**

`LRTConverter.transferAssetFromDepositPool` pulls ERC20 tokens from `LRTDepositPool` into `LRTConverter` and increments `ethValueInWithdrawal` to track their ETH-denominated value: [1](#0-0) 

**Step 2 — The converter balance is excluded from `getTotalAssetDeposits`.**

`LRTDepositPool.getAssetDistributionData` explicitly zeroes out the converter slot for every ERC20 asset, with a comment explaining the ETH-value proxy: [2](#0-1) 

The ETH-value proxy (`ethValueInWithdrawal`) is only surfaced in `getETHDistributionData`, not in the per-ERC20 accounting path. Consequently, `getTotalAssetDeposits(asset)` returns 0 for an ERC20 asset whose entire balance sits in `LRTConverter`.

**Step 3 — `removeSupportedAsset` guard passes incorrectly.**

`LRTConfig.removeSupportedAsset` relies solely on `getTotalAssetDeposits` to block removal: [3](#0-2) 

Because the converter balance is invisible, the guard at line 82 passes even when ERC20 tokens are physically present in `LRTConverter`, and the asset is deleted from `isSupportedAsset`.

**Step 4 — Retrieval path is gated on support.**

`LRTConverter.transferAssetToDepositPool` — the only function that can return ERC20 tokens from `LRTConverter` to `LRTDepositPool` — carries `onlySupportedERC20Token`: [4](#0-3) 

`onlySupportedERC20Token` calls `lrtConfig.isSupportedAsset(asset)`, which now returns `false`. The call reverts, and the tokens have no remaining egress path (there is no `rescueERC20` or equivalent in `LRTConverter`).

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any ERC20 LST (e.g., stETH, ethX) transferred to `LRTConverter` via `transferAssetFromDepositPool` and not yet unstaked becomes permanently irrecoverable once its asset entry is removed from the supported list. The tokens remain in `LRTConverter` with no callable function able to move them out. `transferAssetToDepositPool` reverts, and the unstaking adapters (`unstakeStEth`, `unstakeSwEth`) are asset-specific and do not cover all supported ERC20 tokens.

---

### Likelihood Explanation

The scenario is operationally plausible:

1. The operator sends an ERC20 LST to `LRTConverter` to begin the unstaking pipeline.
2. The unstaking process stalls or the asset is being deprecated.
3. The admin calls `removeSupportedAsset`, trusting the `CannotRemoveAssetWithDeposits` guard to prevent removal if funds remain. The guard silently passes because `assetLyingInConverter = 0`.
4. The asset is removed; tokens are frozen.

No malicious actor is required. The admin acts in good faith, relying on a guard that is documented as the safety net but is structurally incomplete.

---

### Recommendation

`getAssetDistributionData` should query the actual ERC20 balance held by `LRTConverter` instead of hardcoding zero:

```solidity
address lrtConverter = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
assetLyingInConverter = IERC20(asset).balanceOf(lrtConverter);
```

This ensures `getTotalAssetDeposits` reflects the true protocol-wide balance, so `removeSupportedAsset` correctly reverts with `CannotRemoveAssetWithDeposits` whenever ERC20 tokens remain in `LRTConverter`.

---

### Proof of Concept

1. `stETH` is a supported asset. Operator calls `LRTConverter.transferAssetFromDepositPool(stETH, 1000e18)`. `LRTConverter` now holds 1 000 stETH; `ethValueInWithdrawal` is updated.

2. Admin calls `LRTConfig.removeSupportedAsset(stETH, idx)`.
   - `getTotalAssetDeposits(stETH)` → `getAssetDistributionData(stETH)` → `assetLyingInConverter = 0` (line 460 of `LRTDepositPool.sol`).
   - All other balances are 0. Guard at line 82 of `LRTConfig.sol` passes.
   - `isSupportedAsset[stETH]` is deleted.

3. Operator calls `LRTConverter.transferAssetToDepositPool(stETH, 1000e18)`.
   - `onlySupportedERC20Token(stETH)` → `lrtConfig.isSupportedAsset(stETH)` returns `false` → reverts with `AssetNotSupported`.

4. 1 000 stETH are permanently locked in `LRTConverter` with no callable egress.

### Citations

**File:** contracts/LRTConverter.sol (L128-143)
```text
    function transferAssetFromDepositPool(
        address _asset,
        uint256 _amount
    )
        external
        onlySupportedERC20Token(_asset)
        onlyAssetTransferRole
    {
        address lrtDepositPoolAddress = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        ethValueInWithdrawal += (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;

        IERC20(_asset).safeTransferFrom(lrtDepositPoolAddress, address(this), _amount);
    }
```

**File:** contracts/LRTConverter.sol (L149-166)
```text
    function transferAssetToDepositPool(
        address _asset,
        uint256 _amount
    )
        external
        onlySupportedERC20Token(_asset)
        onlyAssetTransferRole
    {
        address lrtDepositPoolAddress = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);
        uint256 assetValue = (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;

        // Set to 0 if assetValue exceeds ethValueInWithdrawal, otherwise subtract assetValue
        ethValueInWithdrawal = ethValueInWithdrawal > assetValue ? ethValueInWithdrawal - assetValue : 0;

        IERC20(_asset).safeTransfer(lrtDepositPoolAddress, _amount);
    }
```

**File:** contracts/LRTDepositPool.sol (L458-461)
```text
        address lrtUnstakingVault = lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT);

        assetLyingInConverter = 0; // assets in converter are accounted in their eth value => getETHDistributionData()
        assetLyingUnstakingVault = IERC20(asset).balanceOf(lrtUnstakingVault);
```

**File:** contracts/LRTConfig.sol (L80-93)
```text
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
```
