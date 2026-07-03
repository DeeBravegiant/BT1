Audit Report

## Title
Unbounded Nested-Loop Gas Consumption in `updateRSETHPrice()` Can Permanently DOS Price Updates - (File: contracts/LRTOracle.sol)

## Summary

`updateRSETHPrice()` is a public, permissionless function whose internal call chain traverses nested loops over supported assets, node delegators, queued EigenLayer withdrawals, and withdrawal strategies. The gas cost scales as `supportedAssets.length × nodeDelegatorQueue.length × queuedWithdrawals.length × strategies.length`. Because `supportedAssets` has no hard cap and `maxNodeDelegatorLimit` is admin-settable with no upper bound, the function can exceed the block gas limit as the protocol grows, permanently freezing price updates and protocol fee minting.

## Finding Description

**Call chain (all code references verified):**

`updateRSETHPrice()` at `LRTOracle.sol:87` is `public whenNotPaused` with no role restriction. It calls `_updateRsETHPrice()` at line 214, which calls `_getTotalEthInProtocol()` at line 231.

`_getTotalEthInProtocol()` (lines 331–349) iterates over every supported asset and calls `ILRTDepositPool.getTotalAssetDeposits(asset)` for each:

```solidity
for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
    uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
    ...
}
```

`getTotalAssetDeposits()` (`LRTDepositPool.sol:385`) calls `getAssetDistributionData()` / `getETHDistributionData()`, both of which loop over every NDC and call `INodeDelegator.getAssetUnstaking(asset)` per NDC (`LRTDepositPool.sol:446–456` and `482–493`):

```solidity
for (uint256 i; i < ndcsCount;) {
    assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);
    ...
}
```

`getAssetUnstaking()` (`NodeDelegator.sol:405–427`) makes an external call to `delegationManager.getQueuedWithdrawals(address(this))` and then iterates over every withdrawal and every strategy within each withdrawal:

```solidity
(IDelegationManager.Withdrawal[] memory queuedWithdrawals, ...) =
    _getDelegationManager().getQueuedWithdrawals(address(this));

for (uint256 withdrawalIndex = 0; withdrawalIndex < queuedWithdrawals.length; withdrawalIndex++) {
    for (uint256 strategyIndex = 0; strategyIndex < withdrawal.strategies.length; strategyIndex++) {
        ...
    }
}
```

**Why existing mitigations are insufficient:**

`LRTUnstakingVault.setMaxUncompletedWithdrawalCount()` caps total queued withdrawals at 80 (`LRTUnstakingVault.sol:151–155`). However:

1. This cap is on the *total* withdrawal count across all NDCs, not on the *product* of all four dimensions. With N assets and M NDCs, `getQueuedWithdrawals()` is called **N × M** times — each call is a full external call loading all withdrawals for that NDC.
2. `supportedAssets` has no hard cap; `addNewSupportedAsset()` in `LRTConfig` is unrestricted in count.
3. `maxNodeDelegatorLimit` is admin-settable with no upper bound (`LRTDepositPool.sol:290–297`).
4. Forced operator undelegations in EigenLayer are not subject to the `maxUncompletedWithdrawalCount` check in `undelegate()` (`NodeDelegator.sol:272–275`), meaning actual queued withdrawals can exceed 80.
5. `updateRSETHPriceAsManager()` (`LRTOracle.sol:94–96`) calls the same `_updateRsETHPrice()` internal function and provides no escape hatch.

## Impact Explanation

If `updateRSETHPrice()` reverts due to block gas exhaustion:

- **Medium. Unbounded gas consumption** — the direct root cause; the function's gas cost has no hard ceiling.
- **Medium. Permanent freezing of unclaimed yield** — protocol fee minting is embedded inside `_updateRsETHPrice()` (lines 299–311); if the function is permanently blocked, no fees can ever be collected.

The `rsETHPrice` also becomes permanently stale, affecting deposit and withdrawal accounting, but this is a secondary consequence of the primary gas DOS.

## Likelihood Explanation

The protocol already supports ETH, stETH, and ETHx, and is designed to grow. `maxNodeDelegatorLimit` defaults to 10 (`LRTDepositPool.sol:49`). With 10 NDCs, 10 supported assets, 80 total queued withdrawals (8 per NDC), and 2 strategies per withdrawal: **10 × 10 = 100 external `getQueuedWithdrawals()` calls** and **10 × 80 × 2 = 1,600 inner loop iterations** per `updateRSETHPrice()` invocation. The team's own comment at `LRTUnstakingVault.sol:151` explicitly acknowledges this gas ceiling, confirming the constraint is a known operational boundary. This is a realistic near-term configuration.

## Recommendation

1. **Cache `getQueuedWithdrawals()` per NDC across all assets.** A single call to `getQueuedWithdrawals(ndc)` can compute the unstaking amount for all assets simultaneously, reducing external calls from `assets × NDCs` to `NDCs`.
2. **Introduce a hard cap on `supportedAssets.length`** analogous to `maxNodeDelegatorLimit`.
3. **Cap the product** `supportedAssets.length × maxNodeDelegatorLimit × maxUncompletedWithdrawalCount` at a value provably within block gas limits, enforced at configuration time.
4. **Decouple price computation from full TVL traversal.** Store per-asset TVL snapshots updated incrementally rather than recomputing the full sum on every price update call.

## Proof of Concept

Call chain triggered by any unprivileged caller:

```
updateRSETHPrice()                          [LRTOracle.sol:87 — public, no auth]
  └─ _updateRsETHPrice()                    [LRTOracle.sol:214]
       └─ _getTotalEthInProtocol()          [LRTOracle.sol:331]
            └─ for each asset (N):          [LRTOracle.sol:336]
                 └─ getTotalAssetDeposits() [LRTDepositPool.sol:385]
                      └─ getAssetDistributionData() / getETHDistributionData()
                           └─ for each NDC (M):  [LRTDepositPool.sol:447 / 484]
                                └─ getAssetUnstaking(asset)  [NodeDelegator.sol:405]
                                     └─ delegationManager.getQueuedWithdrawals()  [external]
                                          └─ for each withdrawal (W):  [NodeDelegator.sol:409]
                                               └─ for each strategy (S):  [NodeDelegator.sol:412]
```

**Foundry fork test plan:**

1. Fork mainnet with a deployed instance of the protocol.
2. Via admin, add 10 supported assets and 10 NDCs (`maxNodeDelegatorLimit = 10`).
3. Via operator, queue 8 withdrawals per NDC (80 total, at the cap).
4. Call `updateRSETHPrice()` with a fixed gas limit of 30,000,000 (current Ethereum block gas limit).
5. Assert the call reverts with out-of-gas.
6. Confirm `updateRSETHPriceAsManager()` also reverts with the same gas limit, demonstrating no privileged escape hatch. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

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
