### Title
Unbounded Nested Loop in `updateRSETHPrice()` Can Permanently Block rsETH Price Updates - (File: contracts/LRTOracle.sol)

### Summary
`LRTOracle.updateRSETHPrice()` is a public function that internally calls `_getTotalEthInProtocol()`, which contains a nested unbounded loop chain: it iterates over all supported assets, and for each asset calls `LRTDepositPool.getTotalAssetDeposits()`, which in turn iterates over all NDCs and calls `NodeDelegator.getAssetUnstaking()` on each — which itself iterates over all queued EigenLayer withdrawals. As the protocol grows, this call chain can exceed the block gas limit, permanently preventing rsETH price updates.

### Finding Description

`LRTOracle._getTotalEthInProtocol()` iterates over every supported asset: [1](#0-0) 

For each asset it calls `ILRTDepositPool.getTotalAssetDeposits(asset)`, which routes to `getAssetDistributionData()`: [2](#0-1) 

And for ETH specifically, `getETHDistributionData()` also loops over all NDCs: [3](#0-2) 

Inside each NDC iteration, `getAssetUnstaking()` is called, which fetches **all** queued EigenLayer withdrawals and iterates over their strategies: [4](#0-3) 

The public entry point has no access control: [5](#0-4) 

The `maxNodeDelegatorLimit` starts at 10 and can be raised by admin with no hard upper bound enforced in the loop path: [6](#0-5) 

`maxUncompletedWithdrawalCount` can be set up to 80 (comment notes 120 was considered): [7](#0-6) 

The combined iteration depth is: `|supportedAssets| × |nodeDelegatorQueue| × |queuedWithdrawals per NDC|`, with each innermost step performing multiple external calls to EigenLayer contracts.

### Impact Explanation

If `updateRSETHPrice()` reverts due to gas exhaustion:
- The stored `rsETHPrice` becomes permanently stale.
- All deposits via `depositETH()` / `depositAsset()` use the stale price to mint rsETH, breaking fair accounting.
- All withdrawals via `LRTWithdrawalManager` use the stale `rsETHPrice` to compute asset payouts.
- Protocol fee minting is blocked.
- `updateRSETHPriceAsManager()` (manager-only) calls the same `_updateRsETHPrice()` and would also fail.

This constitutes **Medium — unbounded gas consumption** that can escalate to **temporary (or permanent) freezing of the price update mechanism**, degrading the protocol's core accounting.

### Likelihood Explanation

The protocol is designed to support multiple LSTs and multiple NDCs. As EigenLayer queued withdrawals accumulate (up to 80 total, distributed across up to 10 NDCs, across multiple supported assets), the gas cost of `updateRSETHPrice()` grows multiplicatively. With 5 supported assets, 10 NDCs, and 8 queued withdrawals per NDC on average, the function already performs ~400 external calls in a single transaction. This is a realistic operational state, not a theoretical edge case.

### Recommendation

1. Cache `getTotalAssetDeposits` results or restructure `_getTotalEthInProtocol` to avoid calling `getAssetUnstaking` (which fetches live EigenLayer state) on every price update.
2. Alternatively, separate the "fetch live EigenLayer unstaking data" step from the price update, storing a periodically-updated snapshot of unstaking amounts.
3. Add a hard cap on `maxNodeDelegatorLimit` and the number of supported assets, and document the gas budget assumptions.

### Proof of Concept

Call path for a single `updateRSETHPrice()` invocation with N assets, M NDCs, K queued withdrawals per NDC:

```
updateRSETHPrice()
  └─ _updateRsETHPrice()
       └─ _getTotalEthInProtocol()                          // LRTOracle.sol:331
            └─ for assetIdx in supportedAssets (N iters)   // LRTOracle.sol:336
                 └─ getTotalAssetDeposits(asset)            // LRTDepositPool.sol:385
                      └─ getAssetDistributionData(asset)
                           └─ for i in nodeDelegatorQueue (M iters)  // LRTDepositPool.sol:447
                                └─ getAssetUnstaking(asset)          // NodeDelegator.sol:405
                                     └─ getQueuedWithdrawals()       // EigenLayer external call
                                     └─ for withdrawal in results (K iters)
                                          └─ strategy.sharesToUnderlyingView()  // external call
```

Total external calls ≈ N × M × (1 + K). With N=5, M=10, K=8: **450 external calls** in one transaction, well within reach of the block gas limit on mainnet.

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

**File:** contracts/LRTDepositPool.sol (L49-49)
```text
        maxNodeDelegatorLimit = 10;
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
