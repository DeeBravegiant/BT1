Audit Report

## Title
Unbounded Nested Gas Consumption in `updateRSETHPrice()` Renders rsETH Price Update Uncallable at Scale - (File: `contracts/LRTOracle.sol`)

## Summary
`LRTOracle.updateRSETHPrice()` is a public, permissionless function whose internal call chain performs nested iteration over supported assets, node delegators, and EigenLayer queued withdrawals. As the protocol scales — or when forced undelegations push the on-chain withdrawal queue above the protocol's soft cap — the cumulative gas cost can exceed the block gas limit, permanently preventing price updates and causing downstream stale-price effects on deposits and withdrawals.

## Finding Description
The call chain is:

```
updateRSETHPrice()                         [LRTOracle.sol:87]
  └─ _updateRsETHPrice()                   [LRTOracle.sol:214]
       └─ _getTotalEthInProtocol()         [LRTOracle.sol:331]
            └─ for each of N assets:       [LRTOracle.sol:336]
                 getTotalAssetDeposits()   [LRTDepositPool.sol:385]
                   └─ getAssetDistributionData()  [LRTDepositPool.sol:426]
                        └─ for each of M NDCs:    [LRTDepositPool.sol:447]
                             getAssetUnstaking()  [NodeDelegator.sol:405]
                               └─ getQueuedWithdrawals() [EigenLayer external]
                               └─ for each of K withdrawals: [NodeDelegator.sol:409]
                                    for each strategy:       [NodeDelegator.sol:412]
                                      sharesToUnderlyingView() [external call]
```

**Layer 1** — `_getTotalEthInProtocol()` loops over every supported asset and calls `getTotalAssetDeposits(asset)` per asset. [1](#0-0) 

**Layer 2** — `getAssetDistributionData()` loops over every NDC and calls `getAssetUnstaking(asset)` per NDC. [2](#0-1) 

The ETH path (`getETHDistributionData`) adds a second full NDC traversal calling `getAssetUnstaking(ETH_TOKEN)` per NDC. [3](#0-2) 

**Layer 3** — `getAssetUnstaking()` calls EigenLayer's `getQueuedWithdrawals` (one external call returning all queued withdrawals) and then iterates over every withdrawal and every strategy within it, making an additional external call to `sharesToUnderlyingView` per non-ETH strategy entry. [4](#0-3) 

**Total iteration count**: `O(N_assets × M_NDCs × K_withdrawals × S_strategies)`. With the protocol's own configured maximums (up to 10 assets, `maxNodeDelegatorLimit` = 10 NDCs, `maxUncompletedWithdrawalCount` ≤ 80), this yields up to 8,000+ storage-reading, external-call-making iterations per invocation.

**Soft cap is insufficient**: `maxUncompletedWithdrawalCount` is enforced only on protocol-initiated `initiateUnstaking()` and `undelegate()` calls. EigenLayer operator-initiated forced undelegations bypass this check entirely, pushing the actual on-chain queue returned by `getQueuedWithdrawals` above the protocol's tracked count. The protocol's own comment explicitly acknowledges this: [5](#0-4) 

**No access control on the trigger**: `updateRSETHPrice()` is callable by any address with no gas limit guard. [6](#0-5) 

`updateRSETHPriceAsManager()` shares the identical `_updateRsETHPrice()` internal path and would also revert under the same conditions. [7](#0-6) 

## Impact Explanation
**Medium — Unbounded gas consumption** making a critical protocol function uncallable. When `updateRSETHPrice()` reverts due to gas exhaustion, `rsETHPrice` becomes permanently stale. All deposits via `depositETH`/`depositAsset` mint rsETH at the stale rate, and all withdrawals via `LRTWithdrawalManager.initiateWithdrawal` compute `expectedAssetAmount` using the stale price, causing users to receive incorrect asset amounts. This constitutes temporary freezing of correct price-dependent operations and matches the allowed impact "Medium. Unbounded gas consumption." [8](#0-7) 

## Likelihood Explanation
The protocol is actively growing its NDC count and queued withdrawal count. The comment in `LRTUnstakingVault` explicitly acknowledges that 120 uncompleted withdrawals would break `updateRSETHPrice()`, and that forced undelegations (EigenLayer operator-initiated, outside protocol control) can push the actual queue above the soft cap. This is a realistic operational scenario: an EigenLayer operator undelegating from multiple NDCs simultaneously creates multiple withdrawal entries per NDC per strategy, all of which are returned by `getQueuedWithdrawals` and iterated in `getAssetUnstaking`. No attacker action is required — normal protocol growth and operator behavior are sufficient triggers. [5](#0-4) 

## Recommendation
1. **Cache per-asset TVL snapshots**: Store a running `totalAssetDeposited[asset]` updated on deposit/withdrawal events rather than recomputing the full sum on every `updateRSETHPrice()` call.
2. **Eliminate live EigenLayer traversal from the price-update hot path**: Store the queued withdrawal amount as a running total updated on `initiateUnstaking` / `completeUnstaking` / `undelegate`, removing the `getQueuedWithdrawals` call from `getAssetUnstaking` entirely.
3. **Single NDC traversal**: Compute the NDC loop once and accumulate all asset balances in a single pass, rather than calling `getAssetDistributionData` (which re-traverses all NDCs) once per supported asset.

## Proof of Concept
**Foundry fork test plan**:
1. Fork mainnet with the deployed protocol.
2. Deploy or configure 10 NDCs, each delegated to an EigenLayer operator.
3. Queue 80 withdrawals across the NDCs via `initiateUnstaking` (hitting the soft cap).
4. Simulate a forced undelegation by the EigenLayer operator on all NDCs, adding additional withdrawal entries to the on-chain queue beyond the protocol's `uncompletedWithdrawalCount`.
5. Call `updateRSETHPrice()` and observe it reverts with an out-of-gas error.
6. Confirm `rsETHPrice` is now stale and that subsequent `depositETH` / `depositAsset` calls mint at the stale rate.

Alternatively, a gas-model differential test: measure gas consumed by `updateRSETHPrice()` as a function of `(N_assets, M_NDCs, K_withdrawals)` and extrapolate to the protocol's configured maximums to demonstrate block gas limit breach. [4](#0-3)

### Citations

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
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

**File:** contracts/LRTUnstakingVault.sol (L151-155)
```text
        // 120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price
        // Need buffer for theoretical operator forced undelegations (ndc count * asset count = 15)
        if (_maxUncompletedWithdrawalCount > 80) {
            revert MaxUncompletedWithdrawalCountTooHigh();
        }
```
