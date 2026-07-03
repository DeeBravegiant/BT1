### Title
Nested Unbounded Loop in `NodeDelegator.getAssetUnstaking()` Called Per-Asset Per-NDC Causes Gas Exhaustion in Deposits and Price Updates - (File: contracts/NodeDelegator.sol)

### Summary

`NodeDelegator.getAssetUnstaking()` contains a nested loop over all EigenLayer-queued withdrawals and their strategies. This function is called for **every NDC for every supported asset** inside `getETHDistributionData()` and `getAssetDistributionData()`, which are themselves called from the publicly reachable `updateRSETHPrice()` and from every user deposit (`depositETH`, `depositAsset`). As the number of supported assets, NDCs, and queued withdrawals grows, the cumulative gas cost can exceed the block gas limit, permanently bricking deposits and price updates.

### Finding Description

`NodeDelegator.getAssetUnstaking()` fetches all queued withdrawals from EigenLayer's `DelegationManager` and iterates over them in a nested loop: [1](#0-0) 

This function is invoked inside `getETHDistributionData()` once per NDC: [2](#0-1) 

And inside `getAssetDistributionData()` once per NDC for every non-ETH supported asset: [3](#0-2) 

`getTotalAssetDeposits()` calls one of these two functions for each supported asset: [4](#0-3) 

`LRTOracle._getTotalEthInProtocol()` calls `getTotalAssetDeposits()` for **every** supported asset in a loop: [5](#0-4) 

`updateRSETHPrice()` is publicly callable with no access restriction: [6](#0-5) 

Every user deposit also triggers the same chain through `_checkIfDepositAmountExceedesCurrentLimit` → `getTotalAssetDeposits`: [7](#0-6) 

The total gas cost scales as **O(assets × NDCs × queued_withdrawals × strategies)**. Each iteration of the inner loop makes two external calls (`strategy.underlyingToken()` and `strategy.sharesToUnderlyingView()`), each costing ≥2,100 gas cold. The protocol's own comment in `LRTUnstakingVault` acknowledges this ceiling: [8](#0-7) 

The comment states "120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price" — but this threshold was computed for a fixed number of NDCs and assets. As either dimension grows (more supported assets added, `maxNodeDelegatorLimit` raised, or EigenLayer forced-undelegations push the real queued-withdrawal count above the protocol counter), the threshold drops and the function reverts out of gas.

### Impact Explanation

If `depositETH()` or `depositAsset()` runs out of gas, users cannot deposit into the protocol. If `updateRSETHPrice()` runs out of gas, the rsETH exchange rate becomes permanently stale, breaking fee accrual and the withdrawal pricing mechanism. Both outcomes constitute a **temporary (potentially permanent) freezing of funds and protocol functionality** with no admin escape hatch that does not require removing NDCs or assets.

### Likelihood Explanation

The protocol already operates with multiple supported assets (stETH, ETHx, sfrxETH, ETH, and potentially more) and up to 10 NDCs. EigenLayer's `maxUncompletedWithdrawalCount` is capped at 80 protocol-wide, but forced undelegations from EigenLayer bypass this counter (acknowledged in the comment). With 6 assets, 10 NDCs, and 80 queued withdrawals distributed across NDCs (each with 2 strategies), the inner loop executes approximately 6 × 80 × 2 = 960 external strategy calls per `updateRSETHPrice()` invocation, consuming several million gas. Adding more assets or NDCs pushes this past the 30M block gas limit.

### Recommendation

1. Cache the result of `getAssetUnstaking()` per NDC across all asset queries within a single call, rather than re-fetching and re-iterating EigenLayer's queued withdrawal list once per asset per NDC.
2. Introduce a `maxLoops` / pagination parameter to `updateRSETHPrice()` and the distribution data functions, analogous to the mitigation suggested in the referenced report.
3. Store a running `assetUnstaking` tally updated incrementally on each `initiateUnstaking` / `completeUnstaking` event, eliminating the need to iterate EigenLayer's queue on every read.

### Proof of Concept

Assume the protocol has 6 supported assets, 10 NDCs, and 80 total queued EigenLayer withdrawals (8 per NDC, each with 2 strategies).

A call to `updateRSETHPrice()` by any external account executes:
- `_getTotalEthInProtocol()` loops over 6 assets.
- For each asset, `getTotalAssetDeposits()` → `getAssetDistributionData()` loops over 10 NDCs.
- For each NDC, `getAssetUnstaking()` calls `DelegationManager.getQueuedWithdrawals()` (external call) and then loops over 8 withdrawals × 2 strategies = 16 external calls to strategy contracts.
- Total external calls: 6 assets × 10 NDCs × (1 `getQueuedWithdrawals` + 16 strategy calls) = 6 × 10 × 17 = **1,020 external calls**.
- At 2,100 gas each (cold): ~2.1M gas for external calls alone, plus loop overhead, storage reads, and ABI decoding of the withdrawal arrays.

If EigenLayer forces an undelegation on all 10 NDCs (adding 10 more withdrawals beyond the protocol counter), the count rises to 90, and the gas cost increases proportionally. Any subsequent call to `depositETH()` by a regular user will revert out of gas, freezing all new deposits.

### Citations

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

**File:** contracts/LRTDepositPool.sol (L447-456)
```text
        for (uint256 i; i < ndcsCount;) {
            assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);

            assetStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetBalance(asset);
            assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);

            unchecked {
                ++i;
            }
        }
```

**File:** contracts/LRTDepositPool.sol (L484-493)
```text
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

**File:** contracts/LRTUnstakingVault.sol (L151-156)
```text
        // 120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price
        // Need buffer for theoretical operator forced undelegations (ndc count * asset count = 15)
        if (_maxUncompletedWithdrawalCount > 80) {
            revert MaxUncompletedWithdrawalCountTooHigh();
        }
        maxUncompletedWithdrawalCount = _maxUncompletedWithdrawalCount;
```
