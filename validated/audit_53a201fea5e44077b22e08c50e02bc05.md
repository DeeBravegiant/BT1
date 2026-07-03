Audit Report

## Title
Nested Unbounded Loop in `_getTotalEthInProtocol()` Can Permanently Freeze rsETH Price Updates - (File: contracts/LRTOracle.sol)

## Summary
`LRTOracle._getTotalEthInProtocol()` drives a compound loop: for each supported asset (no hard cap), it calls `LRTDepositPool.getAssetDistributionData()`, which loops over all NDCs and calls `NodeDelegator.getAssetUnstaking()` per NDC, which in turn calls `getQueuedWithdrawals` and iterates over all queued withdrawals and their strategies. Because `supportedAssetList` has no upper bound and `getQueuedWithdrawals` is invoked once per asset per NDC (not once per NDC), gas cost grows multiplicatively, threatening the liveness of the publicly callable `updateRSETHPrice()`.

## Finding Description

**Level 1 — `_getTotalEthInProtocol()` iterates over all supported assets with no cap:**

`LRTConfig._addNewSupportedAsset()` pushes to `supportedAssetList` with no length guard. [1](#0-0) 

`_getTotalEthInProtocol()` fetches the full list and loops over it, calling `getTotalAssetDeposits(asset)` for each entry. [2](#0-1) 

**Level 2 — `getAssetDistributionData()` loops over all NDCs:**

For each asset, `getAssetDistributionData()` iterates over `nodeDelegatorQueue` and calls `getAssetUnstaking(asset)` on every NDC. [3](#0-2) 

`maxNodeDelegatorLimit` defaults to 10 and is admin-adjustable upward with no ceiling. [4](#0-3) 

**Level 3 — `getAssetUnstaking()` calls `getQueuedWithdrawals` once per (asset, NDC) pair:**

`getAssetUnstaking()` calls `_getDelegationManager().getQueuedWithdrawals(address(this))` and then iterates over every queued withdrawal and every strategy within it. [5](#0-4) 

Critically, this external call is **not cached**: with N supported assets and M NDCs, `getQueuedWithdrawals` is invoked N×M times even though the result for a given NDC is identical across all asset iterations. With 10 assets and 10 NDCs that is 100 redundant `getQueuedWithdrawals` calls before any withdrawal iteration begins.

**The global withdrawal cap does not fully mitigate the issue:**

`maxUncompletedWithdrawalCount` is capped at 80 globally across all NDCs. [6](#0-5) 

The comment at line 151 explicitly acknowledges the gas concern: *"120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price."* This cap addresses the withdrawal dimension but leaves the asset dimension unbounded. With A assets, M NDCs, and W total withdrawals, the total external calls scale as A×M (for `getQueuedWithdrawals`) plus A×W (for `sharesToUnderlyingView`). Adding more supported assets multiplies both terms. [7](#0-6) 

**Both public update paths are affected:**

`updateRSETHPrice()` is callable by anyone and routes through `_updateRsETHPrice()` → `_getTotalEthInProtocol()`. [8](#0-7) 

`updateRSETHPriceAsManager()` (manager-only) calls the same internal path and is equally blocked. [9](#0-8) 

## Impact Explanation

**Medium — Unbounded gas consumption.** As the protocol adds more supported LSTs (no hard cap), the gas cost of `updateRSETHPrice()` grows multiplicatively. If the call exceeds the 30 M block gas limit, `rsETHPrice` becomes permanently stale. All deposit and withdrawal pricing depends on this value: `getRsETHAmountToMint()` divides by `lrtOracle.rsETHPrice()`, so a frozen price causes mispricing for every subsequent deposit and withdrawal. [10](#0-9) 

## Likelihood Explanation

`updateRSETHPrice()` is a permissionless public function. The protocol is designed to support multiple LSTs; governance can add assets at any time via `addNewSupportedAsset()` with no upper-bound check. As the asset list grows, the N×M `getQueuedWithdrawals` call pattern and the N×W `sharesToUnderlyingView` call pattern both scale linearly with asset count. This is a realistic operational scenario as the protocol expands its LST support.

## Recommendation

1. **Cache `getQueuedWithdrawals` per NDC** outside the per-asset loop. Compute each NDC's full withdrawal map once and reuse it for all asset queries, reducing `getQueuedWithdrawals` calls from N×M to M.
2. **Introduce a hard cap on `supportedAssetList`** in `LRTConfig._addNewSupportedAsset()` to bound the outer loop.
3. **Consider incremental TVL accounting**: maintain a running `totalETHInProtocol` updated on each deposit, withdrawal queue, and completion event, eliminating the need for full traversal on every price update.
4. **Alternatively, paginate `_getTotalEthInProtocol()`** so no single transaction must traverse the full state.

## Proof of Concept

1. Governance adds 20 supported assets via `LRTConfig.addNewSupportedAsset()` (no cap prevents this).
2. Admin adds 10 NDCs via `LRTDepositPool.addNodeDelegatorContractToQueue()`.
3. Operators call `NodeDelegator.initiateUnstaking()` until the global `uncompletedWithdrawalCount` reaches 80.
4. Any caller invokes `LRTOracle.updateRSETHPrice()`.
5. The call issues 20×10 = 200 `getQueuedWithdrawals` external calls plus up to 20×80 = 1,600 `sharesToUnderlyingView` calls, plus `getAssetBalance`, `balanceOf`, and oracle price calls for each (asset, NDC) pair — totalling thousands of external calls that can exhaust the 30 M block gas limit.
6. `rsETHPrice` is permanently stale; all subsequent `depositETH()`, `depositAsset()`, and withdrawal pricing use the frozen value.

### Citations

**File:** contracts/LRTConfig.sol (L114-115)
```text
        isSupportedAsset[asset] = true;
        supportedAssetList.push(asset);
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L94-96)
```text
    function updateRSETHPriceAsManager() external onlyLRTManager {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L333-341)
```text
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
```

**File:** contracts/LRTDepositPool.sol (L290-296)
```text
    function updateMaxNodeDelegatorLimit(uint256 maxNodeDelegatorLimit_) external onlyLRTAdmin {
        if (maxNodeDelegatorLimit_ < nodeDelegatorQueue.length) {
            revert InvalidMaximumNodeDelegatorLimit();
        }

        maxNodeDelegatorLimit = maxNodeDelegatorLimit_;
        emit MaxNodeDelegatorLimitUpdated(maxNodeDelegatorLimit);
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

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/NodeDelegator.sol (L406-426)
```text
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
```

**File:** contracts/LRTUnstakingVault.sol (L150-152)
```text
    function setMaxUncompletedWithdrawalCount(uint256 _maxUncompletedWithdrawalCount) external onlyLRTManager {
        // 120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price
        // Need buffer for theoretical operator forced undelegations (ndc count * asset count = 15)
```

**File:** contracts/LRTUnstakingVault.sol (L153-156)
```text
        if (_maxUncompletedWithdrawalCount > 80) {
            revert MaxUncompletedWithdrawalCountTooHigh();
        }
        maxUncompletedWithdrawalCount = _maxUncompletedWithdrawalCount;
```
