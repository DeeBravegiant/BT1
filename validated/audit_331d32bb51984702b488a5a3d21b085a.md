### Title
Unbounded Gas Consumption in `updateRSETHPrice()` via Nested Loops Over Supported Assets, NDCs, and EigenLayer Queued Withdrawals — (File: `contracts/LRTOracle.sol`)

---

### Summary

The publicly callable `LRTOracle.updateRSETHPrice()` function executes a deeply nested chain of loops and external calls whose total gas cost scales as O(assets × NDCs × queued_withdrawals × strategies). As the protocol grows, this function can become uncallable due to block gas limit exhaustion, preventing rsETH price updates and disrupting the entire protocol.

---

### Finding Description

`updateRSETHPrice()` is `public` with only a `whenNotPaused` guard: [1](#0-0) 

It calls `_updateRsETHPrice()` → `_getTotalEthInProtocol()`, which loops over every supported asset: [2](#0-1) 

For each asset, it calls `ILRTDepositPool.getTotalAssetDeposits(asset)` → `getAssetDistributionData()`, which loops over every NDC in `nodeDelegatorQueue`: [3](#0-2) 

For each NDC, it calls `INodeDelegator.getAssetUnstaking(asset)`, which fetches **all** queued EigenLayer withdrawals for that NDC and iterates over them with a nested loop: [4](#0-3) 

The total gas cost is:

```
O(N_assets × N_NDCs × N_queued_withdrawals_per_NDC × N_strategies_per_withdrawal)
```

with each inner iteration performing multiple external calls to EigenLayer contracts (`getQueuedWithdrawals`, `sharesToUnderlyingView`). Critically, `getQueuedWithdrawals(NDC)` is called once per asset per NDC — meaning the same EigenLayer call is repeated `N_assets` times for each NDC, even though the result is asset-independent.

There is **no explicit cap** on `supportedAssetList` (it grows via `TIME_LOCK_ROLE`): [5](#0-4) 

`maxNodeDelegatorLimit` defaults to 10 but is admin-adjustable: [6](#0-5) 

`maxUncompletedWithdrawalCount` is capped at 80: [7](#0-6) 

---

### Impact Explanation

If `updateRSETHPrice()` exceeds the block gas limit, it becomes permanently uncallable. This:

1. Freezes the rsETH price at a stale value, causing all deposits and withdrawals to use an incorrect exchange rate.
2. Disables the protocol's automatic price-drop pause mechanism (which calls `_pause()` on `LRTDepositPool` and `LRTWithdrawalManager` when price drops too far), removing a critical safety guard.
3. Prevents protocol fee minting.

**Impact: Medium — Unbounded gas consumption / Temporary freezing of protocol functionality.**

---

### Likelihood Explanation

The gas cost grows naturally with protocol scale. With 5 assets, 10 NDCs, and 8 queued withdrawals per NDC (total 80), `getQueuedWithdrawals` is called 50 times and the inner loop executes ~400 iterations, each involving external EigenLayer calls. Adding more supported assets (no cap) or increasing `maxNodeDelegatorLimit` linearly multiplies the cost. No attacker action is required — normal protocol growth triggers this.

**Likelihood: Medium.**

---

### Recommendation

1. Cache `getQueuedWithdrawals(NDC)` results so they are fetched once per NDC rather than once per asset per NDC.
2. Introduce an explicit cap on `supportedAssetList` length.
3. Refactor `_getTotalEthInProtocol()` to aggregate all asset unstaking data in a single pass per NDC rather than one pass per (asset, NDC) pair.
4. Consider making `updateRSETHPrice()` accept a subset of assets to process in batches, analogous to the Carapace mitigation of batching `_lendingPools`.

---

### Proof of Concept

Call path for a single invocation of `updateRSETHPrice()` with 5 assets and 10 NDCs each having 8 queued withdrawals:

```
updateRSETHPrice()
└── _getTotalEthInProtocol()
    └── for each of 5 assets:
        └── getTotalAssetDeposits(asset)
            └── getAssetDistributionData(asset)
                └── for each of 10 NDCs:
                    ├── IERC20.balanceOf(NDC)           [external call]
                    ├── getAssetBalance(asset)           [external call → EigenLayer]
                    └── getAssetUnstaking(asset)
                        └── getQueuedWithdrawals(NDC)   [external call → EigenLayer]
                            └── for each of 8 withdrawals:
                                └── for each strategy:
                                    └── sharesToUnderlyingView() [external call]
```

Total `getQueuedWithdrawals` calls: **5 × 10 = 50** (same data fetched 5 times per NDC).
Total inner loop iterations: **5 × 10 × 8 = 400**, each with an external call.
Total external calls: **~500+**, scaling multiplicatively with protocol growth.

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L331-349)
```text
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

            unchecked {
                ++assetIdx;
            }
        }
    }
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

**File:** contracts/LRTConfig.sol (L99-101)
```text
    function addNewSupportedAsset(address asset, uint256 depositLimit) external onlyRole(LRTConstants.TIME_LOCK_ROLE) {
        _addNewSupportedAsset(asset, depositLimit);
    }
```

**File:** contracts/LRTUnstakingVault.sol (L153-156)
```text
        if (_maxUncompletedWithdrawalCount > 80) {
            revert MaxUncompletedWithdrawalCountTooHigh();
        }
        maxUncompletedWithdrawalCount = _maxUncompletedWithdrawalCount;
```
