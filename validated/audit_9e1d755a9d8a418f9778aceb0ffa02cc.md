### Title
`minRsEthAmountToWithdraw` Check Permanently Locks rsETH Tokens Below Minimum Threshold - (File: contracts/LRTWithdrawalManager.sol)

### Summary
Both `initiateWithdrawal` and `instantWithdrawal` in `LRTWithdrawalManager` enforce a per-asset minimum withdrawal amount (`minRsEthAmountToWithdraw[asset]`). Any user whose entire rsETH balance falls below this threshold has no available exit path — their tokens are frozen until the admin lowers the minimum. There is no special-case exception allowing a user to withdraw their full balance even when it is below the configured minimum.

### Finding Description
In `LRTWithdrawalManager.sol`, both withdrawal entry points apply the same hard minimum check:

```solidity
// initiateWithdrawal (line 162)
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}

// instantWithdrawal (line 224)
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}
``` [1](#0-0) [2](#0-1) 

The check is applied uniformly to the requested withdrawal amount, with no exception for the case where `rsETHUnstaked` equals the caller's entire rsETH balance. If a user's total rsETH balance is less than `minRsEthAmountToWithdraw[asset]`, they cannot pass this guard regardless of how much they attempt to withdraw, because they cannot supply more than their balance.

`minRsEthAmountToWithdraw` is a per-asset mapping set by the LRT admin via `setMinRsEthAmountToWithdraw`:

```solidity
function setMinRsEthAmountToWithdraw(address asset, uint256 minRsEthAmountToWithdraw_) external onlyLRTAdmin {
    minRsEthAmountToWithdraw[asset] = minRsEthAmountToWithdraw_;
``` [3](#0-2) 

There is no upper bound on this value and no mechanism for a user to bypass it when withdrawing their full balance.

### Impact Explanation
Any rsETH holder whose balance is below `minRsEthAmountToWithdraw[asset]` for a given asset is completely unable to exit through either the queued (`initiateWithdrawal`) or instant (`instantWithdrawal`) withdrawal paths. Their rsETH tokens are frozen for as long as the minimum remains above their balance. This constitutes a **temporary (potentially permanent) freezing of user funds** with no user-side remedy.

Impact: **Medium — Temporary freezing of funds** (admin can lower the minimum to unblock, but until then user funds are inaccessible).

### Likelihood Explanation
Users can end up with sub-minimum rsETH balances through several realistic paths:

1. The admin raises `minRsEthAmountToWithdraw[asset]` after users have already deposited small amounts.
2. A user receives rsETH via a direct ERC-20 transfer (e.g., from another wallet or a protocol integration) in an amount below the minimum.
3. A user partially withdraws, leaving a residual rsETH balance below the minimum.

Since `minRsEthAmountToWithdraw` defaults to 0 (no minimum) and must be explicitly set, the issue is triggered whenever the admin configures a non-zero minimum — a routine operational action. The combination of a non-zero minimum and any of the above balance scenarios is realistic.

### Recommendation
Mirror the fix suggested in the original report: allow a user to bypass the minimum check when the requested withdrawal amount equals their entire rsETH balance. For example:

```solidity
uint256 userBalance = IERC20(lrtConfig.rsETH()).balanceOf(msg.sender);
if (rsETHUnstaked == 0 || (rsETHUnstaked < minRsEthAmountToWithdraw[asset] && rsETHUnstaked != userBalance)) {
    revert InvalidAmountToWithdraw();
}
```

This preserves the dust-prevention intent of the minimum while ensuring no user is permanently locked out of their full balance.

### Proof of Concept
1. Admin calls `setMinRsEthAmountToWithdraw(stETH, 1e18)` — sets minimum to 1 rsETH.
2. Alice holds `0.5e18` rsETH (received via transfer or left over after a prior partial withdrawal).
3. Alice calls `initiateWithdrawal(stETH, 0.5e18, "")`.
4. The check `0.5e18 < 1e18` is true → `revert InvalidAmountToWithdraw()`.
5. Alice calls `instantWithdrawal(stETH, 0.5e18, "")`.
6. Same check fires → `revert InvalidAmountToWithdraw()`.
7. Alice has no remaining withdrawal path. Her `0.5e18` rsETH is frozen until the admin lowers the minimum. [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L150-178)
```text
    function initiateWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        override
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
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

**File:** contracts/LRTWithdrawalManager.sol (L330-332)
```text
    function setMinRsEthAmountToWithdraw(address asset, uint256 minRsEthAmountToWithdraw_) external onlyLRTAdmin {
        minRsEthAmountToWithdraw[asset] = minRsEthAmountToWithdraw_;
        emit MinAmountToWithdrawUpdated(asset, minRsEthAmountToWithdraw_);
```
