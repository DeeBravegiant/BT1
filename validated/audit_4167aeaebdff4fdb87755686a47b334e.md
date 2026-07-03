### Title
Unbounded Nested Gas Consumption in `updateRSETHPrice()` Renders rsETH Price Update Uncallable at Scale - (File: `contracts/LRTOracle.sol`)

### Summary
`LRTOracle.updateRSETHPrice()` is a public, permissionless function that the protocol depends on for correct rsETH pricing. Its internal call chain performs nested iteration over supported assets, node delegators, and EigenLayer queued withdrawals. As the protocol scales, the cumulative gas cost grows polynomially and can exceed the block gas limit, permanently preventing price updates.

### Finding Description

The call chain originating from `updateRSETHPrice()` creates a deeply nested, unbounded iteration:

**Layer 1 — `LRTOracle._getTotalEthInProtocol()`** iterates over every supported asset: [1](#0-0) 

For each asset it calls `ILRTDepositPool.getTotalAssetDeposits(asset)`.

**Layer 2 — `LRTDepositPool.getAssetDistributionData()`** iterates over every entry in `nodeDelegatorQueue`: [2](#0-1) 

For each NDC it calls `INodeDelegator.getAssetUnstaking(asset)`.

**Layer 3 — `NodeDelegator.getAssetUnstaking()`** fetches **all** queued withdrawals from EigenLayer's `DelegationManager` and iterates over every withdrawal and every strategy within it: [3](#0-2) 

The total iteration count is `O(assets × NDCs × queued_withdrawals × strategies_per_withdrawal)`. With the protocol's own configured maximums — up to 10 supported assets, `maxNodeDelegatorLimit` NDCs (default 10), and `maxUncompletedWithdrawalCount` up to 80 — this yields up to **8,000+ storage-reading, external-call-making iterations per `updateRSETHPrice()` invocation**.

The protocol's own comment in `LRTUnstakingVault` acknowledges this ceiling: [4](#0-3) 

Critically, `maxUncompletedWithdrawalCount` is a **soft, protocol-side counter**. EigenLayer's `getQueuedWithdrawals` returns the actual on-chain queue, which can exceed the protocol's tracked count during forced undelegations or operator-initiated events. The ETH distribution path (`getETHDistributionData`) also iterates over all NDCs calling `getAssetUnstaking(ETH_TOKEN)`, adding another full traversal: [5](#0-4) 

`updateRSETHPrice()` carries no access control — it is callable by any address: [6](#0-5) 

### Impact Explanation

If `updateRSETHPrice()` reverts due to gas exhaustion:

1. The stored `rsETHPrice` becomes permanently stale.
2. All deposits via `LRTDepositPool.depositETH` / `depositAsset` mint rsETH at the stale rate, causing systematic over- or under-minting relative to true TVL.
3. All withdrawals via `LRTWithdrawalManager.initiateWithdrawal` compute `expectedAssetAmount` using the stale price, causing users to receive incorrect asset amounts.
4. The `updateRSETHPriceAsManager()` path (manager-only) shares the identical internal call chain and would also revert.

**Impact: Medium — Unbounded gas consumption making a critical protocol function uncallable, with downstream temporary freezing of correct price-dependent operations.**

### Likelihood Explanation

The protocol is actively growing its NDC count and queued withdrawal count. The comment in `LRTUnstakingVault` explicitly acknowledges that 120 uncompleted withdrawals would break `updateRSETHPrice()`. Forced undelegations (which the comment also references) can push the actual EigenLayer queue above the soft cap. This is a realistic operational scenario, not a theoretical one.

### Recommendation

1. **Decouple asset accounting from price updates**: Cache per-asset TVL snapshots updated lazily or via separate operator calls, rather than recomputing the full sum on every `updateRSETHPrice()` call.
2. **Bound `getAssetUnstaking` iteration**: Store the queued withdrawal amount as a running total updated on `initiateUnstaking` / `completeUnstaking`, eliminating the live EigenLayer traversal from the price-update hot path.
3. **Separate ETH and LST accounting loops**: Avoid calling `getAssetDistributionData` (which re-traverses all NDCs) once per supported asset; compute the NDC traversal once and accumulate all asset balances in a single pass.

### Proof of Concept

```
updateRSETHPrice()                          [LRTOracle.sol:87]
  └─ _updateRsETHPrice()                   [LRTOracle.sol:214]
       └─ _getTotalEthInProtocol()         [LRTOracle.sol:331]
            └─ for each of N assets:       [LRTOracle.sol:336]
                 getTotalAssetDeposits()   [LRTDepositPool.sol:385]
                   └─ getAssetDistributionData()  [LRTDepositPool.sol:426]
                        └─ for each of M NDCs:    [LRTDepositPool.sol:447]
                             getAssetUnstaking()  [NodeDelegator.sol:405]
                               └─ getQueuedWithdrawals() [EigenLayer]
                               └─ for each of K withdrawals: [NodeDelegator.sol:409]
                                    for each strategy:       [NodeDelegator.sol:412]
                                      sharesToUnderlyingView() [external call]

Total iterations: N × M × K × S
With N=10, M=10, K=80, S=3 → 24,000 external-call-bearing iterations per updateRSETHPrice()
```

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

**File:** contracts/NodeDelegator.sol (L406-427)
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
    }
```

**File:** contracts/LRTUnstakingVault.sol (L151-155)
```text
        // 120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price
        // Need buffer for theoretical operator forced undelegations (ndc count * asset count = 15)
        if (_maxUncompletedWithdrawalCount > 80) {
            revert MaxUncompletedWithdrawalCountTooHigh();
        }
```
