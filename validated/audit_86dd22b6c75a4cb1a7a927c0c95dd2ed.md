Audit Report

## Title
Stale Cached `rsETHPrice` in `LRTOracle` Permanently Caps Withdrawers Below Accrued Yield - (File: contracts/LRTWithdrawalManager.sol)

## Summary

`LRTOracle.rsETHPrice` is a cached storage variable updated only when `updateRSETHPrice()` is explicitly called. `LRTWithdrawalManager.initiateWithdrawal()` reads this stale value to compute and permanently store `expectedAssetAmount`. When `unlockQueue()` later processes the request with a refreshed price, `_calculatePayoutAmount` enforces `min(expectedAssetAmount, currentReturn)`, irrecoverably capping the user at the stale lower amount and forfeiting the accrued yield difference.

## Finding Description

`LRTOracle` stores `rsETHPrice` as a persistent state variable updated only on explicit invocation: [1](#0-0) [2](#0-1) 

`initiateWithdrawal` calls `getExpectedAssetAmount`, which reads the cached `lrtOracle.rsETHPrice()` directly: [3](#0-2) [4](#0-3) 

The resulting `expectedAssetAmount` is stored immutably in the `WithdrawalRequest` struct: [5](#0-4) 

When `unlockQueue` is called, `_createUnlockParams` reads the (now-refreshed) `lrtOracle.rsETHPrice()`: [6](#0-5) 

`_calculatePayoutAmount` then returns `min(expectedAssetAmount, currentReturn)`: [7](#0-6) 

If the price rose between initiation and unlock (due to accrued staking rewards), `currentReturn > expectedAssetAmount`, and the user is permanently capped at the stale lower value. In `_unlockWithdrawalRequests`, `assetsCommitted[asset]` is decremented by the original `expectedAssetAmount` while only `payoutAmount` is disbursed — the difference is freed back into the vault rather than returned to the user: [8](#0-7) 

There is no automatic call to `updateRSETHPrice()` inside `initiateWithdrawal`, and the `pricePercentageLimit` guard in `_updateRsETHPrice` can cause a revert for non-manager callers if the price increase exceeds the threshold, making the user-side workaround unreliable: [9](#0-8) 

## Impact Explanation

**High — Theft of unclaimed yield.** Every user who calls `initiateWithdrawal` while `rsETHPrice` is stale receives a permanently reduced `expectedAssetAmount`. The `min()` cap in `_calculatePayoutAmount` makes the shortfall irrecoverable even after the price is updated. The forfeited yield is freed back into the vault and diluted across remaining rsETH holders rather than paid to the withdrawing user. This matches the allowed impact: **High. Theft of unclaimed yield.**

## Likelihood Explanation

`updateRSETHPrice()` is called by off-chain keepers on a periodic schedule, not per-block. EigenLayer staking rewards accrue continuously, so `rsETHPrice` is stale during the entire interval between keeper calls. Any withdrawal initiated in this window — which is the normal operating condition — is affected. No attacker action is required; ordinary users are harmed by routine usage. The window is always open and the impact is proportional to the elapsed time since the last price update.

## Recommendation

Call `_updateRsETHPrice()` (or `updateRSETHPrice()`) at the start of `initiateWithdrawal` before computing `expectedAssetAmount`. Since `updateRSETHPrice()` can revert for non-manager callers when `pricePercentageLimit` is exceeded, the internal `_updateRsETHPrice()` should be invoked directly from within `LRTOracle` via a dedicated internal-or-trusted path, or `initiateWithdrawal` should be moved to call it in a way that bypasses the threshold revert for the withdrawal flow. At minimum, the price must be refreshed before `getExpectedAssetAmount` is called so the stored cap reflects the live rate.

## Proof of Concept

1. EigenLayer staking rewards accrue; true rsETH/ETH rate rises from `1.01e18` to `1.02e18`. Keeper has not yet called `updateRSETHPrice()`.
2. Alice calls `initiateWithdrawal(stETH, 100e18, "")`.
3. `getExpectedAssetAmount` computes `100e18 * 1.01e18 / stETHPrice` → `expectedAssetAmount` stored with stale price.
4. Keeper calls `updateRSETHPrice()` → `rsETHPrice` becomes `1.02e18`.
5. Operator calls `unlockQueue(...)`. `_createUnlockParams` reads `rsETHPrice = 1.02e18`.
6. `_calculatePayoutAmount` computes `currentReturn = 100e18 * 1.02e18 / stETHPrice > expectedAssetAmount`.
7. Returns `expectedAssetAmount` (stale lower value). `assetsCommitted` is decremented by the original amount; only the lower `payoutAmount` is sent to Alice.
8. Alice permanently forfeits the yield difference. The freed assets remain in the vault.

**Foundry fork test plan:** Deploy against a mainnet fork. Simulate a period of EigenLayer reward accrual without calling `updateRSETHPrice()`. Call `initiateWithdrawal` as an unprivileged user. Then call `updateRSETHPrice()` and `unlockQueue`. Assert that `AssetWithdrawalFinalized` emits an `amountReceived` strictly less than `rsETHUnstaked * updatedRsETHPrice / assetPrice`, confirming the yield shortfall.

### Citations

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L260-265)
```text
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
```

**File:** contracts/LRTWithdrawalManager.sol (L168-168)
```text
        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L590-593)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

**File:** contracts/LRTWithdrawalManager.sol (L751-753)
```text
        withdrawalRequests[requestId] = WithdrawalRequest({
            rsETHUnstaked: rsETHUnstaked, expectedAssetAmount: expectedAssetAmount, withdrawalStartBlock: block.number
        });
```

**File:** contracts/LRTWithdrawalManager.sol (L802-807)
```text
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

**File:** contracts/LRTWithdrawalManager.sol (L846-848)
```text
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
```
