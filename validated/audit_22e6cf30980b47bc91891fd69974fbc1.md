### Title
`instantWithdrawal` Condition Check Does Not Account for `assetsCommitted`, Allowing Drain of Assets Reserved for Pending Withdrawal Requests - (File: contracts/LRTWithdrawalManager.sol)

### Summary
The `instantWithdrawal` function checks only `getAssetsAvailableForInstantWithdrawal` (which relies on the manually-set `queuedWithdrawalsBuffer`) but does **not** check `assetsCommitted`. Because `queuedWithdrawalsBuffer` defaults to zero and is never automatically updated when `initiateWithdrawal` is called, any user can drain the entire `LRTUnstakingVault` balance via `instantWithdrawal` even when those assets are already committed to pending queued withdrawal requests, causing those requests to be frozen.

### Finding Description

When a user calls `initiateWithdrawal`, the contract increments `assetsCommitted[asset]` and enforces the invariant that the new request does not exceed `getAvailableAssetAmount`:

```
getAvailableAssetAmount = getTotalAssetDeposits(asset) - assetsCommitted[asset]
``` [1](#0-0) 

`getTotalAssetDeposits` includes `assetLyingUnstakingVault`, so assets sitting in the `LRTUnstakingVault` are counted as backing the commitment. [2](#0-1) 

However, `instantWithdrawal` performs a completely separate check — it queries `unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)`, which is simply `vaultBalance - queuedWithdrawalsBuffer[asset]`: [3](#0-2) [4](#0-3) 

`queuedWithdrawalsBuffer` is set exclusively by `onlyLRTOperator` via `setQueuedWithdrawalsBuffer` and is **never automatically updated** when `initiateWithdrawal` is called. Its default value is `0`. [5](#0-4) 

Because `instantWithdrawal` never reads `assetsCommitted`, it has no knowledge of how much of the vault balance is already spoken for by queued withdrawal requests. With `queuedWithdrawalsBuffer == 0` (the default), the entire vault balance is reported as available for instant withdrawal, even if 100% of it is committed.

### Impact Explanation

An unprivileged user can call `instantWithdrawal` to drain the `LRTUnstakingVault` of assets that are already committed to pending queued withdrawal requests. After the drain:

- `unlockQueue` reads `unstakingVault.balanceOf(asset)` as `totalAvailableAssets` and finds zero, so it cannot unlock any queued requests.
- The rsETH held in the withdrawal manager on behalf of queued-withdrawal users is frozen until the operator manually replenishes the vault (e.g., by completing an EigenLayer withdrawal cycle, which carries a multi-day delay).
- If no other assets exist in the protocol at that moment, the freeze is effectively permanent for those users.

**Impact class**: Temporary (potentially permanent) freezing of funds — Medium.

### Likelihood Explanation

- Instant withdrawals must be enabled by the LRT Manager (`setInstantWithdrawalEnabled`), which is a routine operational action.
- `queuedWithdrawalsBuffer` is `0` by default and requires an explicit operator call to set; many deployments will leave it at zero.
- No special privilege is required for the attacker — any holder of rsETH can call `instantWithdrawal`.
- The attacker does not lose funds; they simply redeem rsETH at the current rate, which is always available to them.

### Recommendation

1. **Automatic buffer accounting**: When `initiateWithdrawal` increments `assetsCommitted[asset]`, also increase `queuedWithdrawalsBuffer[asset]` in the unstaking vault by the same amount; decrease it when the request is unlocked or cancelled.
2. **Cross-check in `instantWithdrawal`**: Before redeeming from the vault, verify that `assetAmountUnlocked ≤ unstakingVault.balanceOf(asset) - assetsCommitted[asset]` (analogous to how `getAvailableAssetAmount` protects `initiateWithdrawal`).

### Proof of Concept

```
State: LRTUnstakingVault holds 100 ETH, deposit pool holds 0 ETH.
       queuedWithdrawalsBuffer[ETH] = 0 (default).
       Instant withdrawals enabled for ETH.

1. Alice calls initiateWithdrawal(ETH, rsETHAmount_A):
   - rsETH transferred to WithdrawalManager.
   - assetsCommitted[ETH] += 100  →  getAvailableAssetAmount(ETH) = 0.

2. Bob calls instantWithdrawal(ETH, rsETHAmount_B):
   - getAssetsAvailableForInstantWithdrawal(ETH) = 100 - 0 = 100  ✓ (passes)
   - assetsCommitted[ETH] is never consulted.
   - Bob burns rsETH, receives 100 ETH from vault.
   - LRTUnstakingVault balance = 0.

3. Operator calls unlockQueue(ETH, ...):
   - totalAvailableAssets = unstakingVault.balanceOf(ETH) = 0.
   - _unlockWithdrawalRequests exits immediately (availableAssetAmount < payoutAmount).
   - Alice's rsETH remains locked in the WithdrawalManager indefinitely.
``` [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L168-173)
```text
        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;
```

**File:** contracts/LRTWithdrawalManager.sol (L212-235)
```text
    function instantWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
        onlyInstantWithdrawalAllowed(asset)
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }
        if (IERC20(lrtConfig.rsETH()).balanceOf(msg.sender) < rsETHUnstaked) revert NotEnoughRsETH();
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }

        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTWithdrawalManager.sol (L283-307)
```text
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

**File:** contracts/LRTUnstakingVault.sol (L196-209)
```text
    /// @notice Set the reserved buffer for queued withdrawals for an asset.
    /// @param asset The asset address.
    /// @param buffer The reserved amount for queued withdrawals.
    function setQueuedWithdrawalsBuffer(
        address asset,
        uint256 buffer
    )
        external
        onlyLRTOperator
        onlySupportedAsset(asset)
    {
        queuedWithdrawalsBuffer[asset] = buffer;
        emit QueuedWithdrawalsBufferUpdated(asset, buffer);
    }
```

**File:** contracts/LRTUnstakingVault.sol (L229-238)
```text
    function getAssetsAvailableForInstantWithdrawal(address asset)
        external
        view
        onlySupportedAsset(asset)
        returns (uint256 availableAmount)
    {
        uint256 vaultBalance = balanceOf(asset);
        uint256 reservedBuffer = queuedWithdrawalsBuffer[asset];
        availableAmount = reservedBuffer >= vaultBalance ? 0 : vaultBalance - reservedBuffer;
    }
```
