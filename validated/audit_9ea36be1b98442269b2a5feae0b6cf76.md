### Title
`initiateWithdrawal` Availability Check Aggregates All Providers While `unlockQueue` Only Draws From a Single Provider — (`File: contracts/LRTWithdrawalManager.sol`)

### Summary

`LRTWithdrawalManager.initiateWithdrawal` gates new withdrawal requests using `getAvailableAssetAmount`, which sums assets across every protocol location (deposit pool, all NodeDelegators, EigenLayer strategies, the unstaking vault, and the converter). However, `unlockQueue` — the only function that actually redeems assets and marks requests as unlockable — exclusively draws from `LRTUnstakingVault.balanceOf(asset)`. Because the protocol's assets are routinely spread across multiple providers, the guard in `initiateWithdrawal` is systematically over-optimistic, allowing users to lock their rsETH into the withdrawal manager for amounts that cannot be serviced until operators complete a multi-step, time-delayed EigenLayer unstaking cycle.

### Finding Description

**Step 1 — Withdrawal initiation uses the aggregate of all providers.**

`initiateWithdrawal` calls `getAvailableAssetAmount`, which delegates to `LRTDepositPool.getTotalAssetDeposits`: [1](#0-0) 

`getTotalAssetDeposits` sums six distinct buckets: [2](#0-1) 

Including EigenLayer-staked and EigenLayer-unstaking amounts that are not yet liquid: [3](#0-2) 

**Step 2 — `unlockQueue` only draws from the unstaking vault (one provider).**

`_createUnlockParams` sets `totalAvailableAssets` exclusively to `unstakingVault.balanceOf(asset)`: [4](#0-3) 

`unlockQueue` then passes this single-provider figure into `_unlockWithdrawalRequests` and redeems only from the vault: [5](#0-4) 

**Step 3 — rsETH is locked in the withdrawal manager from the moment of initiation.**

The user's rsETH is transferred to the contract at initiation time and is not returned until `completeWithdrawal` succeeds: [6](#0-5) 

It is only burned during `unlockQueue`: [7](#0-6) 

**The gap:** In normal protocol operation the vast majority of assets reside in EigenLayer strategies via NodeDelegators. Moving them to the unstaking vault requires `NodeDelegator.initiateUnstaking` followed by `completeUnstaking` after EigenLayer's withdrawal delay (currently ~7 days). During that entire window the user's rsETH is frozen in the withdrawal manager with no recourse.

### Impact Explanation

A depositor who calls `initiateWithdrawal` when most protocol assets are in EigenLayer will have their rsETH locked in `LRTWithdrawalManager` for at least the EigenLayer withdrawal delay (≥7 days) plus any operator latency, even though the `getAvailableAssetAmount` guard reported sufficient availability. This constitutes a **temporary freezing of user funds** (Medium).

### Likelihood Explanation

The normal steady-state of the protocol is that assets are restaked in EigenLayer strategies — that is the protocol's core purpose. Therefore the mismatch between the multi-provider availability check and the single-provider unlock path is triggered on virtually every withdrawal request, not just in edge cases.

### Recommendation

- **Short term:** Replace the `getAvailableAssetAmount` guard in `initiateWithdrawal` with a check against `unstakingVault.balanceOf(asset)` (or a dedicated "liquid reserve" figure) so that the guard reflects what `unlockQueue` can actually service immediately.
- **Long term:** Introduce a unified "available-for-withdrawal" accounting layer that tracks only assets that have already reached the unstaking vault, and enforce that `assetsCommitted` never exceeds this figure. Alternatively, document and enforce that operators must pre-fund the unstaking vault before users are permitted to initiate withdrawals for amounts backed by EigenLayer-locked assets.

### Proof of Concept

1. Protocol has 1 000 ETH total: 950 ETH in EigenLayer strategies (via NodeDelegators), 50 ETH in the unstaking vault.
2. `getAvailableAssetAmount(ETH)` returns `1 000 ETH − 0 committed = 1 000 ETH`.
3. Alice calls `initiateWithdrawal(ETH, rsETHFor900ETH, ...)`. The guard passes (900 < 1 000). Alice's rsETH is transferred to `LRTWithdrawalManager`. `assetsCommitted[ETH] = 900 ETH`.
4. Operator calls `unlockQueue`. `_createUnlockParams` sets `totalAvailableAssets = unstakingVault.balanceOf(ETH) = 50 ETH`. Only 50 ETH worth of requests can be unlocked; Alice's 900 ETH request cannot be processed.
5. To service Alice, the operator must call `NodeDelegator.initiateUnstaking`, wait ≥7 days for EigenLayer's delay, call `completeUnstaking`, then call `unlockQueue` again.
6. Alice's rsETH remains locked in `LRTWithdrawalManager` for the entire duration with no way to cancel or reclaim it. [8](#0-7) [9](#0-8)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-166)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L168-173)
```text
        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;
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

**File:** contracts/LRTWithdrawalManager.sol (L599-603)
```text
    function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
        availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L837-851)
```text
    function _createUnlockParams(
        ILRTOracle lrtOracle,
        ILRTUnstakingVault unstakingVault,
        address asset
    )
        internal
        view
        returns (UnlockParams memory)
    {
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
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

**File:** contracts/LRTDepositPool.sol (L446-456)
```text
        uint256 ndcsCount = nodeDelegatorQueue.length;
        for (uint256 i; i < ndcsCount;) {
            assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);

            assetStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetBalance(asset);
            assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);

            unchecked {
                ++i;
            }
        }
```
