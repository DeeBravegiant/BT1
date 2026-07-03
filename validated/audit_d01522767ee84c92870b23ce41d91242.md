Audit Report

## Title
Stale `getAssetUnstaking()` Valuation Causes Temporary Withdrawal Queue Freeze After Operator Slashing — (`contracts/NodeDelegator.sol`, `contracts/LRTWithdrawalManager.sol`)

## Summary

`NodeDelegator.getAssetUnstaking()` reads current-magnitude-scaled shares from EigenLayer's `getQueuedWithdrawals()` and converts them to an asset amount. When a user calls `LRTWithdrawalManager.initiateWithdrawal()`, `assetsCommitted[asset]` is incremented by the pre-slash amount. If the delegated operator is subsequently slashed before `completeUnstaking()` is called, the vault receives fewer tokens than committed, and `unlockQueue()` breaks on the first request because `availableAssetAmount < payoutAmount`, freezing user funds until the rsETH price oracle is updated to reflect the slashing.

## Finding Description

**Root cause — `getAssetUnstaking()` reflects pre-slash share value at commitment time:**

`NodeDelegator.getAssetUnstaking()` calls `_getDelegationManager().getQueuedWithdrawals(address(this))` and converts the returned shares directly:

```solidity
uint256 sharesToUnstake = withdrawalShares[withdrawalIndex][strategyIndex];
amount += strategyAsset == LRTConstants.ETH_TOKEN
    ? sharesToUnstake
    : strategy.sharesToUnderlyingView(sharesToUnstake);
``` [1](#0-0) 

EigenLayer's `getQueuedWithdrawals` returns shares scaled by the current `maxMagnitude`. Before a slash this equals the full queued amount (e.g., 100 ETH). After a slash it is reduced (e.g., 80 ETH). The vulnerability window is between `initiateWithdrawal()` (pre-slash) and `completeUnstaking()` (post-slash).

**`initiateWithdrawal()` commits based on the inflated pre-slash total:**

`getAvailableAssetAmount()` calls `getTotalAssetDeposits()`, which sums `getAssetUnstaking()` across all NDCs: [2](#0-1) 

```solidity
availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
``` [3](#0-2) 

When the user calls `initiateWithdrawal()`, `assetsCommitted[asset]` is incremented by the pre-slash `expectedAssetAmount` (100 ETH): [4](#0-3) 

**`completeUnstaking()` transfers only the slashed amount to the vault:**

EigenLayer applies the slashing factor internally during `completeQueuedWithdrawal`. The NDC transfers only what it actually received: [5](#0-4) 

The vault now holds 80 ETH, not 100 ETH.

**`unlockQueue()` breaks because vault balance < payoutAmount:**

`_createUnlockParams` sets `totalAvailableAssets` to the actual vault balance (80 ETH): [6](#0-5) 

`_calculatePayoutAmount` returns `min(request.expectedAssetAmount, currentReturn)`: [7](#0-6) 

If the rsETH oracle has not yet been updated to reflect the slashing, `currentReturn = rsETHUnstaked * oldRsETHPrice / assetPrice = 100 ETH`, so `payoutAmount = 100 ETH`. The loop then hits:

```solidity
if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request
``` [8](#0-7) 

80 < 100 → `break` → the request is not unlocked. The freeze persists until the rsETH oracle is updated, at which point `currentReturn` drops to 80 ETH, `payoutAmount = 80 ETH`, and `80 >= 80` allows the unlock to proceed.

**Existing checks are insufficient:** The `initiateWithdrawal()` guard at L170 (`if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw()`) only checks at commitment time and cannot anticipate a future slash. There is no mechanism to adjust `assetsCommitted` or `request.expectedAssetAmount` downward when a slash occurs between commitment and completion.

The developers acknowledge a related edge case in a comment at L149 (`"There is an edge case were the user withdraws last underlying asset and that gets slashed"`), confirming awareness but no mitigation. [9](#0-8) 

## Impact Explanation

**Temporary freezing of funds (Medium).** Users who called `initiateWithdrawal()` before the slash have their withdrawal requests stuck in the queue. The freeze is bounded: it resolves once the rsETH price oracle is updated to reflect the slashing event, after which `payoutAmount` drops to match the vault balance and `unlockQueue()` can proceed. All asset types (not just ETH) are affected because the break at L800 is asset-agnostic.

## Likelihood Explanation

- EigenLayer operator slashing is a legitimate, designed protocol event — no attacker action is required.
- The LRT-rsETH protocol supports multiple operators across multiple NDCs, increasing exposure surface.
- The rsETH price oracle is updated off-chain by operators; a realistic delay exists between a slash event and oracle update.
- The withdrawal delay window (measured in blocks) provides ample time for a slash to occur between `initiateWithdrawal()` and `completeUnstaking()`.
- No privileged access or victim mistake is required; the freeze is a natural consequence of the accounting mismatch.

## Recommendation

1. **In `_unlockWithdrawalRequests()`**: Replace the hard `break` with a partial unlock using `min(payoutAmount, availableAssetAmount)` so that requests can be unlocked at the slashed amount rather than blocking the entire queue.
2. **In `getAssetUnstaking()`**: Use EigenLayer's `getWithdrawableShares()` (which already accounts for current `maxMagnitude`) rather than raw `sharesToUnderlyingView` on the returned shares, so the committed amount is always conservative.
3. **In `initiateWithdrawal()`**: Consider applying a configurable slashing haircut buffer when computing `expectedAssetAmount` from `assetUnstakingFromEigenLayer`, or exclude in-flight EigenLayer withdrawals from the available amount calculation entirely.

## Proof of Concept

```
Mainnet fork test outline (post-EigenLayer slashing upgrade):

1. Deploy/configure LRT-rsETH with one NDC delegated to operator O.
2. NDC deposits 100 stETH into EigenLayer stETH strategy.
3. Operator calls initiateUnstaking() → queues withdrawal of 100 stETH.
   - getAssetUnstaking(stETH) returns 100 stETH.
4. User calls initiateWithdrawal(stETH, rsETH_for_100_stETH).
   - assetsCommitted[stETH] += 100 stETH.
5. EigenLayer slashes operator O by 20% → maxMagnitude: 1e18 → 0.8e18.
6. Operator calls completeUnstaking() → vault receives 80 stETH (not 100).
7. Operator calls unlockQueue(stETH, ...) with stale oracle prices.
   - totalAvailableAssets = unstakingVault.balanceOf(stETH) = 80 stETH
   - payoutAmount = min(100, oldRsETHPrice * rsETHUnstaked / stETHPrice) = 100 stETH
   - 80 < 100 → break → request NOT unlocked
8. Assert: nextLockedNonce[stETH] unchanged; user's request remains locked.
9. Update rsETH oracle to reflect 20% slashing → rsETHPrice drops proportionally.
   - payoutAmount = min(100, newRsETHPrice * rsETHUnstaked / stETHPrice) = 80 stETH
10. Retry unlockQueue() → 80 >= 80 → request unlocked, user receives 80 stETH.
11. Assert: nextLockedNonce[stETH] incremented; user can call completeWithdrawal().
```

### Citations

**File:** contracts/NodeDelegator.sol (L392-394)
```text
                    assets[i].safeTransfer(
                        address(_getUnstakingVault()), assets[i].balanceOf(address(this)) - balancesBefore[i]
                    );
```

**File:** contracts/NodeDelegator.sol (L421-424)
```text
                uint256 sharesToUnstake = withdrawalShares[withdrawalIndex][strategyIndex];
                amount += strategyAsset == LRTConstants.ETH_TOKEN
                    ? sharesToUnstake
                    : strategy.sharesToUnderlyingView(sharesToUnstake);
```

**File:** contracts/LRTDepositPool.sol (L451-451)
```text
            assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);
```

**File:** contracts/LRTWithdrawalManager.sol (L149-149)
```text
    /// has past. There is an edge case were the user withdraws last underlying asset and that asset gets slashed.
```

**File:** contracts/LRTWithdrawalManager.sol (L173-173)
```text
        assetsCommitted[asset] += expectedAssetAmount;
```

**File:** contracts/LRTWithdrawalManager.sol (L599-602)
```text
    function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
        availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
```

**File:** contracts/LRTWithdrawalManager.sol (L800-800)
```text
            if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request
```

**File:** contracts/LRTWithdrawalManager.sol (L833-834)
```text
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```

**File:** contracts/LRTWithdrawalManager.sol (L849-849)
```text
            totalAvailableAssets: unstakingVault.balanceOf(asset)
```
