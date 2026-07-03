### Title
`getAvailableAssetAmount` Counts Illiquid EigenLayer-Queued Assets, Allowing `initiateWithdrawal` to Accept Requests That `unlockQueue` Cannot Service - (`contracts/LRTWithdrawalManager.sol`)

### Summary

`getAvailableAssetAmount` gates `initiateWithdrawal` using `lrtDepositPool.getTotalAssetDeposits(asset)`, which includes assets currently in EigenLayer's withdrawal queue (`assetUnstakingFromEigenLayer`). However, `unlockQueue` sources its `totalAvailableAssets` exclusively from `unstakingVault.balanceOf(asset)` — the actual liquid vault balance. This mismatch allows withdrawal requests to be accepted against assets that are not yet liquid, causing `unlockQueue` to stall on those requests until the EigenLayer withdrawal completes.

### Finding Description

**Step 1 — `getAvailableAssetAmount` includes illiquid assets** [1](#0-0) 

It calls `lrtDepositPool.getTotalAssetDeposits(asset)`: [2](#0-1) 

`getTotalAssetDeposits` sums `assetStakedInEigenLayer + assetUnstakingFromEigenLayer`, where `assetUnstakingFromEigenLayer` is populated by: [3](#0-2) 

`getAssetUnstaking` reads live queued withdrawals from EigenLayer's `DelegationManager` — these tokens are not in the vault and are not liquid. [4](#0-3) 

**Step 2 — `initiateWithdrawal` accepts the request** [5](#0-4) 

The check `expectedAssetAmount > getAvailableAssetAmount(asset)` passes because the illiquid EigenLayer-queued amount inflates `totalAssets`. `assetsCommitted[asset]` is incremented and the user's rsETH is taken.

**Step 3 — `unlockQueue` uses only the liquid vault balance** [6](#0-5) 

`totalAvailableAssets` is set to `unstakingVault.balanceOf(asset)` — the raw ERC-20 balance of the vault, which does **not** include assets still in EigenLayer's withdrawal queue.

**Step 4 — `_unlockWithdrawalRequests` stalls** [7](#0-6) 

If `unstakingVault.balanceOf(asset) < payoutAmount`, the loop breaks immediately. The user's request remains locked until an operator calls `completeUnstaking` on the NodeDelegator (after EigenLayer's withdrawal delay), which transfers assets into the vault, and then `unlockQueue` is called again.

### Impact Explanation

The user's rsETH is locked at `initiateWithdrawal` time. After the protocol's own `withdrawalDelayBlocks` (~8 days) elapses, the user expects to call `completeWithdrawal`, but `unlockQueue` cannot process their request because the vault has no liquid balance. The user must wait an additional EigenLayer withdrawal delay on top of the protocol delay. No funds are permanently lost, but the promised return timeline is violated.

**Scoped impact: Low — Contract fails to deliver promised returns, but doesn't lose value.**

### Likelihood Explanation

This is a normal operational state. Operators routinely call `initiateUnstaking` to queue EigenLayer withdrawals as part of the rebalancing/unstaking workflow. During the EigenLayer withdrawal delay window, `getAssetUnstaking` returns non-zero values, inflating `getAvailableAssetAmount`. Any user who calls `initiateWithdrawal` during this window against an asset whose vault balance is insufficient will be affected. No special permissions or adversarial action are required — only normal protocol operation.

### Recommendation

`getAvailableAssetAmount` should be aligned with what `unlockQueue` can actually service. Replace the `getTotalAssetDeposits` call with `unstakingVault.balanceOf(asset)` (minus already-committed amounts), or subtract `assetUnstakingFromEigenLayer` from the total before computing availability:

```solidity
function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
    ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(
        lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT)
    );
    uint256 liquidBalance = unstakingVault.balanceOf(asset);
    availableAssetAmount = liquidBalance > assetsCommitted[asset]
        ? liquidBalance - assetsCommitted[asset]
        : 0;
}
```

This ensures `initiateWithdrawal` only accepts requests that `unlockQueue` can immediately service from the vault.

### Proof of Concept

```
Fork mainnet (or local fork with EigenLayer contracts).

1. Operator calls NodeDelegator.initiateUnstaking(strategies, shares)
   → EigenLayer queues withdrawal; getAssetUnstaking(asset) > 0
   → unstakingVault.balanceOf(asset) == 0

2. User calls LRTWithdrawalManager.initiateWithdrawal(asset, rsETHAmount, "")
   → getAvailableAssetAmount returns > 0 (inflated by assetUnstakingFromEigenLayer)
   → assetsCommitted[asset] += expectedAssetAmount
   → rsETH transferred from user ✓

3. After withdrawalDelayBlocks, operator calls unlockQueue(asset, ...)
   → _createUnlockParams: totalAvailableAssets = unstakingVault.balanceOf(asset) == 0
   → revert AmountMustBeGreaterThanZero  (or loop breaks immediately if vault has dust)

4. Assert: user cannot complete withdrawal despite delay having passed.

5. Operator calls NodeDelegator.completeUnstaking(...) after EigenLayer delay
   → assets land in unstakingVault
   → unlockQueue now succeeds

Assert: user experienced an additional delay beyond the protocol-promised window.
```

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L168-173)
```text
        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;
```

**File:** contracts/LRTWithdrawalManager.sol (L599-603)
```text
    function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
        availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L797-800)
```text
            // Calculate the amount user will receive
            uint256 payoutAmount = _calculatePayoutAmount(request, rsETHPrice, assetPrice);

            if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request
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

**File:** contracts/LRTDepositPool.sol (L450-451)
```text
            assetStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetBalance(asset);
            assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);
```

**File:** contracts/NodeDelegator.sol (L405-427)
```text
    function getAssetUnstaking(address asset) external view returns (uint256 amount) {
        (IDelegationManager.Withdrawal[] memory queuedWithdrawals, uint256[][] memory withdrawalShares) =
            _getDelegationManager().getQueuedWithdrawals(address(this));

        for (uint256 withdrawalIndex = 0; withdrawalIndex < queuedWithdrawals.length; withdrawalIndex++) {
            IDelegationManager.Withdrawal memory withdrawal = queuedWithdrawals[withdrawalIndex];

            for (uint256 strategyIndex = 0; strategyIndex < withdrawal.strategies.length; strategyIndex++) {
                IStrategy strategy = withdrawal.strategies[strategyIndex];

                address strategyAsset = address(strategy) == address(lrtConfig.beaconChainETHStrategy())
                    ? LRTConstants.ETH_TOKEN
                    : address(strategy.underlyingToken());

                if (strategyAsset != asset) continue;

                uint256 sharesToUnstake = withdrawalShares[withdrawalIndex][strategyIndex];
                amount += strategyAsset == LRTConstants.ETH_TOKEN
                    ? sharesToUnstake
                    : strategy.sharesToUnderlyingView(sharesToUnstake);
            }
        }
    }
```
