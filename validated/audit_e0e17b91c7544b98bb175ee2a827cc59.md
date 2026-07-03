### Title
`getAssetUnstaking` Uses Raw `scaledShares` Without Slashing Factor, Causing `getAvailableAssetAmount` to Overestimate Withdrawable ETH — (File: contracts/NodeDelegator.sol)

---

### Summary

`NodeDelegator.getAssetUnstaking` returns raw EigenLayer `scaledShares` for the beacon-chain ETH strategy without multiplying by the operator's `maxMagnitude × beaconChainScalingFactor`. After a slashing event, this inflates `getTotalAssetDeposits`, which inflates `getAvailableAssetAmount`, which allows users to commit more ETH to the withdrawal queue than will ever arrive from EigenLayer. When the EigenLayer withdrawal finally completes and delivers less ETH than accounted for, the unstaking vault is short, and later withdrawal requests cannot be unlocked — permanently or temporarily freezing those users' funds.

---

### Finding Description

`NodeDelegator.getAssetUnstaking` iterates over all queued EigenLayer withdrawals and sums the withdrawable amount per asset:

```solidity
// NodeDelegator.sol lines 421-424
uint256 sharesToUnstake = withdrawalShares[withdrawalIndex][strategyIndex];
amount += strategyAsset == LRTConstants.ETH_TOKEN
    ? sharesToUnstake                              // ← raw scaledShares, no slashing factor
    : strategy.sharesToUnderlyingView(sharesToUnstake);
``` [1](#0-0) 

`withdrawalShares` is the second return value of `IDelegationManager.getQueuedWithdrawals`, which exposes the `scaledShares` field of the `Withdrawal` struct. EigenLayer's own documentation in the interface states:

> "Note that these scaledShares need to be multiplied by the operator's maxMagnitude and beaconChainScalingFactor at completion to include slashing occurring during the queue withdrawal delay." [2](#0-1) 

For LST strategies the code delegates to `strategy.sharesToUnderlyingView`, which internally applies the strategy's exchange rate and therefore reflects slashing. For the beacon-chain ETH strategy the code uses `sharesToUnstake` directly, so slashing is silently ignored.

`LRTDepositPool.getTotalAssetDeposits` aggregates this overestimated value:

```solidity
// LRTDepositPool.sol lines 394-396
uint256 effectiveAssetWithEigenLayer = assetStakedInEigenLayer + assetUnstakingFromEigenLayer;
return (assetLyingInDepositPool + assetLyingInNDCs + effectiveAssetWithEigenLayer
        + assetLyingInConverter + assetLyingUnstakingVault);
``` [3](#0-2) 

`LRTWithdrawalManager.getAvailableAssetAmount` then uses this inflated total to gate new withdrawal requests:

```solidity
// LRTWithdrawalManager.sol lines 600-602
uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
availableAssetAmount = totalAssets > assetsCommitted[asset]
    ? totalAssets - assetsCommitted[asset] : 0;
``` [4](#0-3) 

And `initiateWithdrawal` uses it to decide whether to accept a new request:

```solidity
// LRTWithdrawalManager.sol lines 170-173
if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
assetsCommitted[asset] += expectedAssetAmount;
``` [5](#0-4) 

When `unlockQueue` is later called, it uses the **actual** vault balance, not the inflated estimate:

```solidity
// LRTWithdrawalManager.sol line 849
totalAvailableAssets: unstakingVault.balanceOf(asset)
``` [6](#0-5) 

If slashing reduced the ETH that arrived in the vault, `unlockQueue` can only service a subset of the committed requests. The remaining requests are stuck.

---

### Impact Explanation

**Temporary (potentially permanent) freezing of user funds.**

Users who initiated withdrawal requests after slashing occurred — but while `getAssetUnstaking` still reported the pre-slashing amount — will have their requests locked in the queue indefinitely. The vault will never receive enough ETH to cover the over-committed `assetsCommitted[ETH]`, so `unlockQueue` will exit early on every call once the real balance is exhausted. Affected users cannot complete their withdrawals and cannot reclaim their rsETH (it was already transferred to the withdrawal manager at `initiateWithdrawal` time).

---

### Likelihood Explanation

EigenLayer slashing is an explicit, documented risk of restaking. The withdrawal delay (currently 7–14 days in EigenLayer) creates a window during which slashing can reduce the actual ETH delivered. The protocol already acknowledges this risk in a comment:

> "There is an edge case were the user withdraws last underlying asset and that asset gets slashed." [7](#0-6) 

Any unprivileged user can call `initiateWithdrawal` at any time. No special role or front-running is required — the user simply needs to call the function while the inflated `getAvailableAssetAmount` is visible on-chain.

---

### Recommendation

Replace the raw `sharesToUnstake` path for the beacon-chain ETH strategy with a call that applies the operator's current `maxMagnitude` and `beaconChainScalingFactor`, mirroring how EigenLayer itself computes the withdrawable amount at completion time. Alternatively, use `IDelegationManager.getWithdrawableShares` (if available in the deployed EigenLayer version) which already returns the slashing-adjusted value, and use that result in `getAssetUnstaking` instead of the raw `scaledShares`.

---

### Proof of Concept

1. Operator stakes 100 ETH via `NodeDelegator.stake32Eth` and verifies credentials.
2. Operator calls `NodeDelegator.initiateUnstaking` — 100 ETH worth of `scaledShares` enters the EigenLayer withdrawal queue.
3. EigenLayer slashing event reduces the operator's `maxMagnitude` by 50 %; actual ETH to be received drops to 50 ETH.
4. `NodeDelegator.getAssetUnstaking(ETH)` still returns 100 ETH (raw `scaledShares`).
5. `LRTDepositPool.getTotalAssetDeposits(ETH)` returns 100 ETH.
6. `LRTWithdrawalManager.getAvailableAssetAmount(ETH)` returns 100 ETH.
7. User A calls `initiateWithdrawal(ETH, rsETH_A)` → commits 60 ETH; `assetsCommitted[ETH]` = 60.
8. User B calls `initiateWithdrawal(ETH, rsETH_B)` → commits 40 ETH; `assetsCommitted[ETH]` = 100.
9. Operator calls `NodeDelegator.completeUnstaking` — only 50 ETH arrives in `LRTUnstakingVault`.
10. Operator calls `LRTWithdrawalManager.unlockQueue(ETH, ...)` — `totalAvailableAssets` = 50 ETH; only User A's 60 ETH request partially fits (or only one user is served), User B's request is never unlocked.
11. User B calls `completeWithdrawal` → reverts with `WithdrawalLocked` indefinitely; their rsETH is permanently held by the withdrawal manager.

### Citations

**File:** contracts/NodeDelegator.sol (L421-424)
```text
                uint256 sharesToUnstake = withdrawalShares[withdrawalIndex][strategyIndex];
                amount += strategyAsset == LRTConstants.ETH_TOKEN
                    ? sharesToUnstake
                    : strategy.sharesToUnderlyingView(sharesToUnstake);
```

**File:** contracts/external/eigenlayer/interfaces/IDelegationManager.sol (L123-133)
```text
        // Array containing the amount of staker's scaledShares for withdrawal in each Strategy in the `strategies`
        // array
        // Note that these scaledShares need to be multiplied by the operator's maxMagnitude and
        // beaconChainScalingFactor at completion to include
        // slashing occurring during the queue withdrawal delay. This is because scaledShares = sharesToWithdraw /
        // (maxMagnitude * beaconChainScalingFactor)
        // at queue time. beaconChainScalingFactor is simply equal to 1 if the strategy is not the beaconChainStrategy.
        // To account for slashing, we later multiply scaledShares * maxMagnitude * beaconChainScalingFactor at the
        // earliest possible completion time
        // to get the withdrawn shares after applying slashing during the delay period.
        uint256[] scaledShares;
```

**File:** contracts/LRTDepositPool.sol (L394-396)
```text
        uint256 effectiveAssetWithEigenLayer = assetStakedInEigenLayer + assetUnstakingFromEigenLayer;
        return (assetLyingInDepositPool + assetLyingInNDCs + effectiveAssetWithEigenLayer + assetLyingInConverter
                + assetLyingUnstakingVault);
```

**File:** contracts/LRTWithdrawalManager.sol (L147-150)
```text
    /// @dev This function is only callable by the user and is used to initiate a withdrawal request for a specific
    /// asset. Will be finalised by calling `completeWithdrawal` after the manager unlocked the request and the delay
    /// has past. There is an edge case were the user withdraws last underlying asset and that asset gets slashed.
    function initiateWithdrawal(
```

**File:** contracts/LRTWithdrawalManager.sol (L170-173)
```text
        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;
```

**File:** contracts/LRTWithdrawalManager.sol (L600-602)
```text
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
        availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
```

**File:** contracts/LRTWithdrawalManager.sol (L846-850)
```text
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
```
