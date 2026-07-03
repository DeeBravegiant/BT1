Audit Report

## Title
`assetsCommitted` exceeding `getTotalAssetDeposits` after EigenLayer slashing blocks all new withdrawal requests — (`contracts/LRTWithdrawalManager.sol`)

## Summary

`initiateWithdrawal` checks that `expectedAssetAmount > getAvailableAssetAmount(asset)` before queuing a withdrawal. `getAvailableAssetAmount` returns `max(0, totalAssets − assetsCommitted[asset])`. EigenLayer slashing reduces `totalAssets` (via `assetStakedInEigenLayer` in `getTotalAssetDeposits`) without touching `assetsCommitted`, which is only decremented inside `_unlockWithdrawalRequests` when an operator calls `unlockQueue`. Once `assetsCommitted > totalAssets`, every `initiateWithdrawal` call reverts with `ExceedAmountToWithdraw`, and `unlockQueue` itself reverts with `AmountMustBeGreaterThanZero` if the unstaking vault is empty, extending the freeze for the full EigenLayer withdrawal delay.

## Finding Description

`initiateWithdrawal` executes the following sequence:

```solidity
// L166
IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
// L168
uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
// L170
if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
// L173
assetsCommitted[asset] += expectedAssetAmount;
```

`getAvailableAssetAmount` (L599–603) computes:

```solidity
uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
```

`getTotalAssetDeposits` (L385–397) sums `assetLyingInDepositPool + assetLyingInNDCs + assetStakedInEigenLayer + assetUnstakingFromEigenLayer + assetLyingInConverter + assetLyingUnstakingVault`. EigenLayer slashing directly reduces `assetStakedInEigenLayer`, lowering `totalAssets` without any corresponding reduction to `assetsCommitted[asset]`.

`assetsCommitted[asset]` is decremented only at L802 inside `_unlockWithdrawalRequests`, which is called exclusively by the operator-gated `unlockQueue`. `unlockQueue` itself reads `totalAvailableAssets: unstakingVault.balanceOf(asset)` (L849) and reverts at L297 with `AmountMustBeGreaterThanZero` when that balance is zero — i.e., while assets are still traversing EigenLayer's delayed-withdrawal queue. The freeze therefore persists for the full EigenLayer withdrawal delay after a slashing event.

## Impact Explanation

All unprivileged users are blocked from calling `initiateWithdrawal` for the slashed asset. Their rsETH cannot be redeemed through the normal withdrawal path for the duration of the freeze. This is **temporary freezing of funds** (Medium), consistent with the allowed impact scope.

## Likelihood Explanation

EigenLayer slashing is an explicitly documented and expected risk of restaking. The protocol delegates to multiple node operators via `NodeDelegator`, and any single slashing event that reduces `getTotalAssetDeposits` below the current `assetsCommitted` value triggers the freeze. Under normal high-demand conditions `assetsCommitted` can approach `totalAssets`, so even a modest slashing event is sufficient to cross the threshold. No attacker action is required; the freeze arises from normal protocol operation combined with an EigenLayer slashing event.

## Recommendation

Decouple the availability check from the committed-vs-total comparison, or allow `initiateWithdrawal` to proceed when the protocol is in an over-committed state by letting `unlockQueue` settle payouts at the prevailing (potentially reduced) price via the existing `_calculatePayoutAmount` cap. Alternatively, reset `assetsCommitted[asset]` to `min(assetsCommitted[asset], totalAssets)` at the start of `initiateWithdrawal` so that slashing-induced shortfalls do not permanently block new requests.

## Proof of Concept

1. `getTotalAssetDeposits(ETH)` = 100 ETH; `assetsCommitted[ETH]` = 0.
2. Users call `initiateWithdrawal` until `assetsCommitted[ETH]` = 95 ETH; `getAvailableAssetAmount` = 5 ETH.
3. EigenLayer slashing reduces the NDC's strategy balance by 10 ETH; `getTotalAssetDeposits(ETH)` drops to 90 ETH.
4. `getAvailableAssetAmount` = `max(0, 90 − 95)` = **0** (L602).
5. Any `initiateWithdrawal` call with any non-zero `rsETHUnstaked` hits L170 and reverts with `ExceedAmountToWithdraw`.
6. Operator attempts `unlockQueue`; `_createUnlockParams` reads `unstakingVault.balanceOf(asset)` = 0 (assets still in EigenLayer delayed-withdrawal queue); L297 reverts with `AmountMustBeGreaterThanZero`.
7. Freeze persists until EigenLayer's withdrawal delay completes, assets arrive in the unstaking vault, and an operator successfully calls `unlockQueue` to decrement `assetsCommitted` at L802.

Foundry fork test plan: fork mainnet, deploy/configure the protocol, simulate high `assetsCommitted` via multiple `initiateWithdrawal` calls, call EigenLayer's slashing mechanism on the relevant strategy to reduce shares, then assert that `initiateWithdrawal` reverts and `unlockQueue` reverts while the unstaking vault is empty. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-173)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;
```

**File:** contracts/LRTWithdrawalManager.sol (L297-297)
```text
        if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero();
```

**File:** contracts/LRTWithdrawalManager.sol (L599-603)
```text
    function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
        availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L797-802)
```text
            // Calculate the amount user will receive
            uint256 payoutAmount = _calculatePayoutAmount(request, rsETHPrice, assetPrice);

            if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request

            assetsCommitted[asset] -= request.expectedAssetAmount;
```

**File:** contracts/LRTWithdrawalManager.sol (L846-850)
```text
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
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
