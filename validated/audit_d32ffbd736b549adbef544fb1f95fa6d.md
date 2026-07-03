Audit Report

## Title
Missing `expectedAssetAmount > 0` Validation Allows rsETH to Be Burned for Zero Assets - (File: contracts/LRTWithdrawalManager.sol)

## Summary
Both `initiateWithdrawal` and `instantWithdrawal` validate only that `rsETHUnstaked != 0` when `minRsEthAmountToWithdraw[asset]` is at its default value of `0`. Because `getExpectedAssetAmount` performs integer division, a sufficiently small `rsETHUnstaked` value truncates to zero. In `instantWithdrawal`, rsETH is burned before any check on the computed output amount, so the user's rsETH is permanently destroyed while they receive zero underlying assets.

## Finding Description
`minRsEthAmountToWithdraw` is a mapping that defaults to `0` for every asset: [1](#0-0) 

Both entry points share the same guard: [2](#0-1) [3](#0-2) 

When `minRsEthAmountToWithdraw[asset] == 0` (the default), this reduces to `rsETHUnstaked == 0`, so any value ≥ 1 wei passes. The output is then computed via integer division: [4](#0-3) 

When `rsETHUnstaked * rsETHPrice < assetPrice`, the result truncates to `0`.

**`instantWithdrawal` path:** rsETH is burned at line 229 *before* any check on `assetAmountUnlocked`. The subsequent availability check `assetAmountUnlocked > available` evaluates `0 > X` which is always false, so execution continues. `redeem(asset, 0)` is called, fee is `0`, and `_transferAsset(asset, msg.sender, 0)` delivers nothing: [5](#0-4) 

**`initiateWithdrawal` path:** `expectedAssetAmount = 0` passes the `> getAvailableAssetAmount` check (0 is never greater), `assetsCommitted[asset] += 0`, and the request is stored with `expectedAssetAmount = 0`: [6](#0-5) 

When `unlockQueue` later processes the request, `_calculatePayoutAmount` computes `min(0, currentReturn)` which is `0`, so `rsETHAmountToBurn += request.rsETHUnstaked` while `assetAmountToUnlock += 0`: [7](#0-6) [8](#0-7) 

## Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.** The user's rsETH is permanently burned while they receive zero underlying assets. The protocol's TVL is unaffected (the burned rsETH with no corresponding asset withdrawal marginally benefits remaining rsETH holders), but the individual caller loses their rsETH with no recourse.

## Likelihood Explanation
The condition requires only that `minRsEthAmountToWithdraw[asset] == 0` (the default for every asset unless explicitly configured by an admin) and that `rsETHUnstaked * rsETHPrice < assetPrice`. Since rsETH accrues yield and its price is typically slightly below that of the underlying LST (e.g., stETH), the truncation-to-zero condition is reachable with `rsETHUnstaked = 1 wei`. Any unprivileged user holding any rsETH balance can trigger this on either withdrawal path without any special access.

## Recommendation
Add an explicit output validation immediately after computing the expected amount in both `initiateWithdrawal` and `instantWithdrawal`:

```solidity
uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
if (expectedAssetAmount == 0) revert InvalidAmountToWithdraw();
```

Additionally, `setMinRsEthAmountToWithdraw` should enforce a non-zero minimum for all supported assets at the time they are added, rather than relying on a post-deployment admin call.

## Proof of Concept
1. Admin adds stETH as a supported asset with instant withdrawal enabled; `minRsEthAmountToWithdraw[stETH]` remains `0` (default).
2. Assume `rsETHPrice = 1.05e18`, `stETHPrice = 1.06e18` (realistic: rsETH slightly trails stETH).
3. User calls `instantWithdrawal(stETH, 1, "")` (1 wei rsETH).
4. Guard passes: `1 != 0 && 1 >= 0`.
5. `assetAmountUnlocked = 1 * 1.05e18 / 1.06e18 = 0` (integer truncation).
6. `burnFrom(user, 1)` executes — 1 wei rsETH is permanently destroyed.
7. `0 > getAssetsAvailableForInstantWithdrawal(stETH)` is false — no revert.
8. `redeem(stETH, 0)`, fee = 0, `_transferAsset(stETH, user, 0)` — user receives nothing.

**Foundry fuzz test plan:** Fuzz `rsETHUnstaked` over `[1, assetPrice/rsETHPrice - 1]` with mocked oracle prices where `rsETHPrice < assetPrice`. Assert that after `instantWithdrawal`, the user's rsETH balance decreased by `rsETHUnstaked` while their asset balance is unchanged.

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

**File:** contracts/LRTWithdrawalManager.sol (L168-175)
```text
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

**File:** contracts/LRTWithdrawalManager.sol (L833-834)
```text
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```
