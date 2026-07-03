### Title
`instantWithdrawal` Bypasses `assetsCommitted` Accounting Check, Enabling Temporary Freezing of Pending Withdrawal Requests - (File: contracts/LRTWithdrawalManager.sol)

---

### Summary

`LRTWithdrawalManager` has two withdrawal paths. The normal queued path (`initiateWithdrawal`) enforces a critical `assetsCommitted` check to prevent over-commitment of protocol assets. The alternative fast-track path (`instantWithdrawal`) skips this check entirely, allowing any user to drain assets from the `LRTUnstakingVault` that are already committed to pending withdrawal requests, temporarily freezing those requests.

---

### Finding Description

The normal withdrawal flow in `initiateWithdrawal` enforces:

```solidity
uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
assetsCommitted[asset] += expectedAssetAmount;
```

`getAvailableAssetAmount` computes `totalAssets - assetsCommitted[asset]`, where `assetsCommitted` tracks the total assets already promised to users who have initiated but not yet completed withdrawals. This prevents the protocol from over-committing assets that are already spoken for.

The `instantWithdrawal` path does **not** check `assetsCommitted` at all:

```solidity
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(...);
if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
    revert CantInstantWithdrawMoreThanAvailable();
}
unstakingVault.redeem(asset, assetAmountUnlocked);
```

It only checks `getAssetsAvailableForInstantWithdrawal` on the unstaking vault — a completely separate check that does not account for `assetsCommitted`. This means an instant withdrawal user can redeem assets from the `LRTUnstakingVault` that are already committed to pending queued withdrawal requests.

The `unlockQueue` operator function also redeems from the same vault:

```solidity
unstakingVault.redeem(asset, assetAmountUnlocked);
```

If the vault is drained by instant withdrawals, `unlockQueue` cannot fulfill pending requests, temporarily freezing those users' funds until the operator replenishes the vault (e.g., by moving assets from the deposit pool or waiting for EigenLayer withdrawal delays to expire).

---

### Impact Explanation

**Medium — Temporary freezing of funds.**

Users who have called `initiateWithdrawal` and are waiting for `unlockQueue` to process their requests have their expected asset amounts tracked in `assetsCommitted`. If instant withdrawal users drain the `LRTUnstakingVault` of those committed assets, the operator's `unlockQueue` call will fail to redeem the required assets, leaving pending withdrawal requests in a locked state until the vault is replenished. If the remaining protocol assets are in EigenLayer (subject to a multi-day withdrawal delay), the freeze can persist for days.

---

### Likelihood Explanation

**Medium.** The `instantWithdrawal` path is gated by `onlyInstantWithdrawalAllowed(asset)`, which requires the manager to have enabled it. When enabled (a legitimate operational state), any rsETH holder can call `instantWithdrawal` and exploit the missing `assetsCommitted` check. No privileged role compromise is required beyond the manager having enabled the feature.

---

### Recommendation

In `instantWithdrawal`, add a check against `getAvailableAssetAmount(asset)` (which accounts for `assetsCommitted`) before redeeming from the unstaking vault, analogous to the check in `initiateWithdrawal`:

```solidity
if (assetAmountUnlocked > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
```

Alternatively, decrement `assetsCommitted` is not needed here (since rsETH is burned immediately), but the availability check must use the same `assetsCommitted`-aware accounting to prevent draining committed assets.

---

### Proof of Concept

1. User A calls `initiateWithdrawal(ETH, 80e18, "")` — `assetsCommitted[ETH] = 80 ETH`. Protocol has 100 ETH in the unstaking vault.
2. `getAvailableAssetAmount(ETH)` now returns `100 - 80 = 20 ETH`. A second `initiateWithdrawal` for 30 ETH would revert with `ExceedAmountToWithdraw`.
3. User B calls `instantWithdrawal(ETH, rsETHFor100ETH, "")`. `getAssetsAvailableForInstantWithdrawal` returns 100 ETH (full vault balance, unaware of `assetsCommitted`). The call succeeds and drains all 100 ETH from the unstaking vault.
4. Operator calls `unlockQueue(ETH, ...)`. It attempts `unstakingVault.redeem(ETH, 80e18)` but the vault is empty — the call fails or processes zero requests.
5. User A's withdrawal request is frozen until the operator replenishes the unstaking vault from the deposit pool or waits for EigenLayer withdrawal delays. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L161-178)
```text
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }

        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);

        emit ReferralIdEmitted(referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L212-253)
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

        uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
        uint256 userAmount = assetAmountUnlocked - fee;

        address feeRecipient = instantWithdrawalFeeRecipient;
        if (feeRecipient == address(0)) {
            // Backwards-compatible default: send fees to the protocol treasury
            feeRecipient = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        }
        if (fee > 0) {
            _transferAsset(asset, feeRecipient, fee);
            emit InstantWithdrawalFeeCollected(msg.sender, asset, fee);
        }

        _transferAsset(asset, msg.sender, userAmount);
        emit ReferralIdEmitted(referralId);
        emit AssetWithdrawalFinalized(msg.sender, asset, rsETHUnstaked, userAmount);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L299-308)
```text
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
