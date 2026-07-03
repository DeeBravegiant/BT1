### Title
Unbounded Nested-Loop Gas Consumption in `_getTotalEthInProtocol()` and `getAssetDistributionData()` Renders Deposits and Price Updates Uncallable - (File: contracts/LRTOracle.sol, contracts/LRTDepositPool.sol, contracts/NodeDelegator.sol)

---

### Summary

`LRTOracle.updateRSETHPrice()` is a public, permissionless function. It internally calls `_getTotalEthInProtocol()`, which iterates over every supported asset, and for each asset calls `LRTDepositPool.getTotalAssetDeposits()` → `getAssetDistributionData()` / `getETHDistributionData()`, which in turn iterates over every NDC in `nodeDelegatorQueue` and calls `NodeDelegator.getAssetUnstaking()` on each. `getAssetUnstaking()` itself performs two nested loops: one over all EigenLayer-queued withdrawals for that NDC, and one over all strategies inside each withdrawal. The same nested-loop path is triggered by the user-facing `depositETH()` and `depositAsset()` functions. Because `maxNodeDelegatorLimit` has no enforced upper bound and the number of supported assets is also uncapped, the aggregate gas cost grows multiplicatively and can exceed the block gas limit, permanently bricking price updates and temporarily freezing user deposits.

---

### Finding Description

**Call chain for `updateRSETHPrice()` (public, no role check):**

```
updateRSETHPrice()
  └─ _getTotalEthInProtocol()                          // loops over supportedAssets[]
       └─ getTotalAssetDeposits(asset)                 // per asset
            └─ getAssetDistributionData(asset)         // loops over nodeDelegatorQueue[]
                 └─ INodeDelegator.getAssetUnstaking() // per NDC, per asset
                      └─ delegationManager.getQueuedWithdrawals()  // external call
                           └─ nested loop: withdrawals × strategies
``` [1](#0-0) 

**`_getTotalEthInProtocol()`** iterates over `supportedAssets` (no hard cap) and for each calls `getTotalAssetDeposits()`. [2](#0-1) 

**`getAssetDistributionData()`** iterates over `nodeDelegatorQueue` and calls `getAssetUnstaking()` on every NDC for every supported asset. [3](#0-2) 

**`getETHDistributionData()`** does the same for ETH, called by `depositETH()` via `_checkIfDepositAmountExceedesCurrentLimit()`. [4](#0-3) 

**`getAssetUnstaking()`** fetches *all* queued withdrawals from EigenLayer for the NDC (regardless of asset) and runs a nested loop over withdrawals × strategies. This is called once per NDC per supported asset, so the total work is `O(supportedAssets × NDCs × queuedWithdrawals × strategies)`.

**Missing upper bound on `maxNodeDelegatorLimit`:** [5](#0-4) 

`updateMaxNodeDelegatorLimit()` only checks that the new limit is ≥ the current queue length; there is no ceiling. An admin can legitimately raise it to accommodate protocol growth, and the queue can be filled to that limit.

**Call chain for user deposits:** [6](#0-5) 

`_checkIfDepositAmountExceedesCurrentLimit()` calls `getTotalAssetDeposits()`, which triggers the same nested-loop path. Both `depositETH()` and `depositAsset()` go through this check on every invocation.

---

### Impact Explanation

**Medium — Temporary freezing of funds / Unbounded gas consumption.**

As the protocol scales (more supported assets, more NDCs, more EigenLayer queued withdrawals), the gas cost of `updateRSETHPrice()` and `depositETH()` / `depositAsset()` grows multiplicatively. At realistic but achievable values — e.g., 5 supported assets, 10 NDCs (the default `maxNodeDelegatorLimit`), 80 queued withdrawals per NDC (the maximum `maxUncompletedWithdrawalCount`), and 5 strategies per withdrawal — the inner work is `5 × 10 × 80 × 5 = 20,000` iterations, each involving external storage reads from EigenLayer. This can exceed Ethereum's block gas limit (~30M gas), making:

1. `updateRSETHPrice()` permanently uncallable → rsETH price becomes stale → fee accrual and price-based deposit/withdrawal calculations break.
2. `depositETH()` / `depositAsset()` uncallable → user deposits are temporarily frozen until the NDC count or queued withdrawal count is reduced.

---

### Likelihood Explanation

**Medium.** The protocol is designed to scale: more LSTs can be added as supported assets, more NDCs can be added to distribute restaking load, and EigenLayer queued withdrawals accumulate during normal operation. No single privileged action is required to trigger the condition — it emerges from ordinary protocol growth. The `maxNodeDelegatorLimit` has no ceiling, and `maxUncompletedWithdrawalCount` is capped at 80 per NDC, meaning the worst-case gas cost is already reachable within the protocol's own parameter space.

---

### Recommendation

1. **Cap `maxNodeDelegatorLimit`** with an absolute ceiling (e.g., 15–20) enforced in `updateMaxNodeDelegatorLimit()`.
2. **Cache `getQueuedWithdrawals()` results** per NDC rather than calling it once per supported asset per NDC. A single call per NDC can accumulate balances for all assets simultaneously.
3. **Decouple TVL accounting from the hot path**: store per-NDC asset balances in a lazily-updated mapping updated only when withdrawals are initiated or completed, rather than recomputing from EigenLayer state on every deposit and price update.
4. **Add a gas guard** or pagination to `_getTotalEthInProtocol()` so that if the loop cannot complete within a safe gas budget, it reverts with a clear error rather than silently running out of gas.

---

### Proof of Concept

1. Admin adds 10 NDCs (default `maxNodeDelegatorLimit = 10`).
2. Each NDC accumulates 80 queued EigenLayer withdrawals (the maximum allowed by `maxUncompletedWithdrawalCount`).
3. Protocol has 5 supported assets.
4. Any user calls `updateRSETHPrice()`.
5. Execution path: `_getTotalEthInProtocol()` loops 5 assets → for each, `getAssetDistributionData()` loops 10 NDCs → for each NDC, `getAssetUnstaking()` calls `getQueuedWithdrawals()` (returning 80 withdrawals) and loops over them → total: 5 × 10 × 80 = 4,000 external storage reads plus inner strategy loops.
6. At ~2,100 gas per cold SLOAD and multiple SLOADs per withdrawal entry, this easily exceeds 30M gas, causing the transaction to revert with out-of-gas.
7. The same path is hit by any call to `depositETH()` or `depositAsset()`, freezing user deposits.

### Citations

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

**File:** contracts/LRTDepositPool.sol (L290-297)
```text
    function updateMaxNodeDelegatorLimit(uint256 maxNodeDelegatorLimit_) external onlyLRTAdmin {
        if (maxNodeDelegatorLimit_ < nodeDelegatorQueue.length) {
            revert InvalidMaximumNodeDelegatorLimit();
        }

        maxNodeDelegatorLimit = maxNodeDelegatorLimit_;
        emit MaxNodeDelegatorLimitUpdated(maxNodeDelegatorLimit);
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

**File:** contracts/LRTDepositPool.sol (L482-493)
```text
        uint256 ndcsCount = nodeDelegatorQueue.length;

        for (uint256 i; i < ndcsCount;) {
            ethLyingInNDCs += nodeDelegatorQueue[i].balance;

            ethStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getEffectivePodShares();
            ethUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i])
                .getAssetUnstaking(LRTConstants.ETH_TOKEN);
            unchecked {
                ++i;
            }
        }
```

**File:** contracts/LRTDepositPool.sol (L676-682)
```text
    function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (asset == LRTConstants.ETH_TOKEN) {
            return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
        }
        return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
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
