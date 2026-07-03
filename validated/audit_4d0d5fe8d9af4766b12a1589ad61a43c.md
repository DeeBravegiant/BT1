### Title
Unbounded Gas Consumption in `updateRSETHPrice()` via Nested Loops Over Supported Assets, NDCs, and EigenLayer Queued Withdrawals — (File: contracts/LRTOracle.sol)

---

### Summary

`LRTOracle.updateRSETHPrice()` is a public, permissionless function. Its execution cost scales as `O(supportedAssets × NDCs × queuedWithdrawals × strategies)` due to nested loops and repeated external calls to EigenLayer's `DelegationManager.getQueuedWithdrawals()`. Under realistic protocol parameters, this can approach or exceed the block gas limit, permanently preventing rsETH price updates and breaking the deposit/withdrawal lifecycle for all users.

---

### Finding Description

The call chain triggered by any caller of `updateRSETHPrice()` is:

1. `LRTOracle.updateRSETHPrice()` (public, no access control) → `_updateRsETHPrice()` → `_getTotalEthInProtocol()`
2. `_getTotalEthInProtocol()` loops over every supported asset and calls `ILRTDepositPool.getTotalAssetDeposits(asset)` for each.
3. `getTotalAssetDeposits()` calls `getAssetDistributionData(asset)`, which loops over every NDC in `nodeDelegatorQueue` and calls `INodeDelegator(ndc).getAssetUnstaking(asset)` for each.
4. `NodeDelegator.getAssetUnstaking()` calls `_getDelegationManager().getQueuedWithdrawals(address(this))` — a full external call to EigenLayer that returns all queued withdrawals for that NDC — and then iterates over every withdrawal and every strategy within each withdrawal.

The total work is:

```
supportedAssets × NDCs × getQueuedWithdrawals() calls
+ supportedAssets × NDCs × queuedWithdrawals × strategies_per_withdrawal  (inner loop iterations)
```

With protocol-allowed maximums (`maxNodeDelegatorLimit` = 10, `maxUncompletedWithdrawalCount` = 80, ~5 supported assets, ~2 strategies per withdrawal), this yields up to **50 external calls to EigenLayer** and **~8 000 inner loop iterations** in a single transaction. Each `getQueuedWithdrawals()` call loads the full withdrawal queue from EigenLayer storage, making the gas cost very high.

Critically, `getQueuedWithdrawals()` is called once per `(asset, NDC)` pair even though the result is identical for a given NDC regardless of asset, multiplying the cost by `supportedAssets` unnecessarily.

---

### Impact Explanation

If the cumulative gas cost of `updateRSETHPrice()` exceeds the block gas limit, the function becomes permanently uncallable — including via `updateRSETHPriceAsManager()`, which calls the same `_updateRsETHPrice()` path. A stale `rsETHPrice` stored in `LRTOracle` is then used for all subsequent `getRsETHAmountToMint()` and `getExpectedAssetAmount()` calculations, causing depositors to receive incorrect rsETH amounts and withdrawers to receive incorrect asset amounts. In the worst case, the price oracle is permanently frozen, constituting a **Medium: Unbounded gas consumption** finding with secondary risk of **Low: Contract fails to deliver promised returns**.

---

### Likelihood Explanation

The protocol explicitly allows up to 80 uncompleted withdrawals (`maxUncompletedWithdrawalCount`) and up to 10 NDCs (`maxNodeDelegatorLimit`). These are independent admin-controlled parameters. As the protocol scales — more NDCs added, more EigenLayer withdrawal rounds queued — the gas cost of `updateRSETHPrice()` grows multiplicatively. No single parameter needs to be set to an extreme value; the product of normal operational values is sufficient to approach the gas limit. This is a realistic scenario during periods of high unstaking activity.

---

### Recommendation

1. **Cache `getQueuedWithdrawals()` per NDC**: In `_getTotalEthInProtocol()` or `getAssetDistributionData()`, call `getQueuedWithdrawals()` once per NDC and reuse the result across all assets, eliminating the `supportedAssets`-fold redundancy.
2. **Decouple price update from full TVL scan**: Store per-asset TVL snapshots updated lazily, rather than recomputing the full sum on every `updateRSETHPrice()` call.
3. **Bound the product**: Enforce that `maxNodeDelegatorLimit × maxUncompletedWithdrawalCount × supportedAssets` stays within a safe gas budget, not just each parameter individually.

---

### Proof of Concept

**Entry point — public, no access control:** [1](#0-0) 

**Outer loop over supported assets in `_getTotalEthInProtocol()`:** [2](#0-1) 

**Inner loop over NDCs in `getAssetDistributionData()`, calling `getAssetUnstaking()` per NDC per asset:** [3](#0-2) 

**`getAssetUnstaking()` — calls EigenLayer `getQueuedWithdrawals()` and iterates over all queued withdrawals and their strategies:** [4](#0-3) 

**`maxUncompletedWithdrawalCount` capped at 80 (but multiplied across NDCs × assets):** [5](#0-4) 

**`maxNodeDelegatorLimit` initialized to 10:** [6](#0-5)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L336-348)
```text
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
```

**File:** contracts/LRTDepositPool.sol (L49-50)
```text
        maxNodeDelegatorLimit = 10;
        lrtConfig = ILRTConfig(lrtConfigAddr);
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

**File:** contracts/LRTUnstakingVault.sol (L151-156)
```text
        // 120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price
        // Need buffer for theoretical operator forced undelegations (ndc count * asset count = 15)
        if (_maxUncompletedWithdrawalCount > 80) {
            revert MaxUncompletedWithdrawalCountTooHigh();
        }
        maxUncompletedWithdrawalCount = _maxUncompletedWithdrawalCount;
```
