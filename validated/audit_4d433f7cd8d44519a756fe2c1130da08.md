### Title
Missing `expectedAssetAmount > 0` Validation Allows rsETH to Be Burned for Zero Assets - (File: contracts/LRTWithdrawalManager.sol)

### Summary

`LRTWithdrawalManager.initiateWithdrawal` and `instantWithdrawal` validate that `rsETHUnstaked` is non-zero and meets the minimum threshold, but never validate that the computed output amount (`expectedAssetAmount` / `assetAmountUnlocked`) is greater than zero. When `minRsEthAmountToWithdraw[asset]` is at its default value of `0` and a user submits a `rsETHUnstaked` value small enough that integer division truncates to zero, the user's rsETH is permanently burned while they receive zero underlying assets.

### Finding Description

Both withdrawal entry points perform the same input check:

```solidity
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}
``` [1](#0-0) [2](#0-1) 

`minRsEthAmountToWithdraw` is a mapping that defaults to `0` for every asset: [3](#0-2) 

So the guard reduces to `rsETHUnstaked == 0`, meaning any value ≥ 1 wei passes. The output amount is then computed via integer division:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
``` [4](#0-3) 

When `rsETHUnstaked * rsETHPrice < assetPrice` (e.g., `rsETHUnstaked = 1 wei`, `rsETHPrice = 1.05e18`, `assetPrice = 1.06e18`), the division truncates to `0`.

In `instantWithdrawal`, rsETH is burned **before** any check on `assetAmountUnlocked`:

```solidity
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked); // burned here
ILRTUnstakingVault unstakingVault = ...;
if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
    revert CantInstantWithdrawMoreThanAvailable();
}
// 0 > X is false → passes
unstakingVault.redeem(asset, assetAmountUnlocked); // redeems 0
...
_transferAsset(asset, msg.sender, userAmount); // transfers 0
``` [5](#0-4) 

In `initiateWithdrawal`, the rsETH is transferred to the contract and the withdrawal request is stored with `expectedAssetAmount = 0`. When `unlockQueue` later processes it, the rsETH is burned and the user receives zero assets: [6](#0-5) 

The `_calculatePayoutAmount` function returns `min(0, currentReturn) = 0`, so `assetAmountToUnlock += 0` while `rsETHAmountToBurn += request.rsETHUnstaked`: [7](#0-6) 

### Impact Explanation

A user calling `instantWithdrawal` or `initiateWithdrawal` with a `rsETHUnstaked` value that produces `expectedAssetAmount = 0` permanently loses their rsETH while receiving zero underlying assets. The contract accepts the call, burns the rsETH, and delivers nothing. This matches **Low — Contract fails to deliver promised returns, but doesn't lose value** (the protocol's TVL is unaffected; only the individual user's rsETH is destroyed).

### Likelihood Explanation

The condition requires `minRsEthAmountToWithdraw[asset] == 0` (the default for every asset unless explicitly configured by admin) and `rsETHUnstaked` small enough that `rsETHUnstaked * rsETHPrice < assetPrice`. Since rsETH accrues yield, `rsETHPrice` is typically slightly below `assetPrice` for LSTs (e.g., stETH), making the truncation-to-zero condition reachable with `rsETHUnstaked = 1 wei`. Any user with rsETH balance can trigger this without any privileged access.

### Recommendation

Add an explicit output validation immediately after computing the expected amount in both functions:

```solidity
uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
if (expectedAssetAmount == 0) revert InvalidAmountToWithdraw();
```

Additionally, `setMinRsEthAmountToWithdraw` should enforce a non-zero minimum for all supported assets at the time they are added, rather than relying on a post-deployment admin call.

### Proof of Concept

1. Admin adds stETH as a supported asset with instant withdrawal enabled; `minRsEthAmountToWithdraw[stETH]` remains `0` (default).
2. Assume `rsETHPrice = 1.05e18`, `stETHPrice = 1.06e18` (realistic: rsETH slightly trails stETH).
3. User calls `instantWithdrawal(stETH, 1, "")` (1 wei rsETH).
4. `assetAmountUnlocked = 1 * 1.05e18 / 1.06e18 = 0` (integer truncation).
5. `burnFrom(user, 1)` executes — 1 wei rsETH is destroyed.
6. `redeem(stETH,

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L35-35)
```text
    mapping(address asset => uint256) public minRsEthAmountToWithdraw;
```

**File:** contracts/LRTWithdrawalManager.sol (L162-164)
```text
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }
```

**File:** contracts/LRTWithdrawalManager.sol (L166-175)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);
```

**File:** contracts/LRTWithdrawalManager.sol (L224-226)
```text
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }
```

**File:** contracts/LRTWithdrawalManager.sol (L228-250)
```text
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
```

**File:** contracts/LRTWithdrawalManager.sol (L593-593)
```text
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

**File:** contracts/LRTWithdrawalManager.sol (L798-808)
```text
            uint256 payoutAmount = _calculatePayoutAmount(request, rsETHPrice, assetPrice);

            if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request

            assetsCommitted[asset] -= request.expectedAssetAmount;
            // Set the amount the user will receive
            request.expectedAssetAmount = payoutAmount;
            rsETHAmountToBurn += request.rsETHUnstaked;
            availableAssetAmount -= payoutAmount;
            assetAmountToUnlock += payoutAmount;

```
