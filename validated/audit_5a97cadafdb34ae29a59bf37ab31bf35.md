### Title
Unbounded Gas Consumption in `updateRSETHPrice()` via Deeply Nested External Calls Across Assets, NDCs, and Queued Withdrawals - (File: contracts/LRTOracle.sol)

### Summary
`LRTOracle.updateRSETHPrice()` is publicly callable and internally executes a deeply nested call chain that multiplies external calls across supported assets × node delegators × queued EigenLayer withdrawals × strategies per withdrawal. As the protocol scales, this function can exceed the block gas limit, permanently freezing the rsETH price oracle.

### Finding Description

`LRTOracle.updateRSETHPrice()` is a public function with no access control that calls `_updateRsETHPrice()`, which calls `_getTotalEthInProtocol()`. [1](#0-0) 

`_getTotalEthInProtocol()` loops over every supported asset and for each makes two external calls: [2](#0-1) 

The call to `ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset)` resolves to `getAssetDistributionData()`, which itself loops over every NDC and makes **three external calls per NDC per asset**: [3](#0-2) 

The third call, `INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset)`, is the most expensive. It calls `DelegationManager.getQueuedWithdrawals()` and then iterates over **all queued withdrawals × all strategies per withdrawal**, making additional external calls to `strategy.sharesToUnderlyingView()` for each non-ETH strategy: [4](#0-3) 

The same pattern applies to `getETHDistributionData()` (called when the ETH token is in the supported asset list), which also calls `getAssetUnstaking(ETH_TOKEN)` per NDC: [5](#0-4) 

**Worst-case call count** (using protocol-configured maximums):
- Supported assets: unbounded (admin-controlled, currently ~3–5)
- NDCs: up to `maxNodeDelegatorLimit` = 10
- Queued withdrawals per NDC: up to `maxUncompletedWithdrawalCount` = 80
- Strategies per withdrawal: variable

At 10 NDCs × 80 queued withdrawals × N strategies, each asset iteration in `_getTotalEthInProtocol()` can trigger 800+ external calls. With multiple supported assets, total external calls can reach thousands per `updateRSETHPrice()` invocation. [6](#0-5) 

### Impact Explanation

If `updateRSETHPrice()` exceeds the block gas limit it becomes permanently uncallable. This has cascading effects:

1. **rsETH price freezes**: `rsETHPrice` is a stored value only updated by this function. A frozen price means the protocol's fee minting mechanism (`protocolFeeInETH` calculation and `IRSETH.mint` to treasury) is permanently blocked — **theft of unclaimed yield**.
2. **Price protection disabled**: The downside protection that pauses the protocol on price drops (`isPriceDecreaseOffLimit`) can never trigger, removing a critical safety mechanism.
3. **Deposit mispricing**: `getRsETHAmountToMint` reads `lrtOracle.rsETHPrice()` directly; a permanently stale price causes incorrect rsETH minting for all depositors. [7](#0-6) [8](#0-7) 

### Likelihood Explanation

`updateRSETHPrice()` is publicly callable with no access control. The gas cost grows automatically as the protocol operates normally — operators queue withdrawals via `initiateUnstaking()` and `undelegate()`, which are routine operational actions. No attacker action is required; normal protocol growth (more NDCs, more queued withdrawals) naturally pushes gas cost toward the block limit. With `maxUncompletedWithdrawalCount` set to 80 and `maxNodeDelegatorLimit` at 10, the worst-case scenario is reachable under normal operations.

### Recommendation

1. **Cache the `INodeDelegator` reference** outside the inner loop and avoid repeated `lrtConfig` lookups per iteration.
2. **Decouple `getAssetUnstaking` from `_getTotalEthInProtocol`**: Instead of computing live queued withdrawal amounts on every price update, maintain an on-chain accounting variable (similar to `uncompletedWithdrawalCount`) that tracks the total unstaking amount per asset, updated incrementally on `initiateUnstaking` and `completeUnstaking`.
3. **Cap the number of supported assets** with an explicit maximum, analogous to `maxNodeDelegatorLimit`.
4. **Consider a keeper/off-chain aggregation pattern** where the total ETH value is submitted by a trusted off-chain actor and validated on-chain, rather than computed fully on-chain in a single transaction.

### Proof of Concept

Call trace for a single `updateRSETHPrice()` with 3 supported assets, 10 NDCs, and 80 queued withdrawals per NDC:

```
updateRSETHPrice()
└── _updateRsETHPrice()
    └── _getTotalEthInProtocol()                          // LRTOracle.sol:331
        ├── lrtConfig.getSupportedAssetList()             // 1 external call
        └── [for each of 3 assets]
            ├── getAssetPrice(asset)                      // 1 ext call (price oracle)
            └── getTotalAssetDeposits(asset)              // LRTDepositPool.sol:385
                └── getAssetDistributionData(asset)       // LRTDepositPool.sol:426
                    └── [for each of 10 NDCs]
                        ├── IERC20(asset).balanceOf(NDC)  // 1 ext call
                        ├── NDC.getAssetBalance(asset)    // 1 ext call → DelegationManager
                        └── NDC.getAssetUnstaking(asset)  // NodeDelegator.sol:405
                            └── DelegationManager.getQueuedWithdrawals()  // 1 ext call
                                └── [for each of 80 withdrawals]
                                    └── [for each strategy]
                                        └── strategy.sharesToUnderlyingView()  // 1 ext call

// Total external calls (conservative, 1 strategy per withdrawal):
// 1 + 3 × (1 + 10 × (1 + 1 + 1 + 80)) = 1 + 3 × (1 + 10 × 83) = 1 + 3 × 831 = ~2,494 external calls
```

At ~2,100 gas per external call (cold) this alone approaches ~5.2M gas, before accounting for EigenLayer's internal computation within `getQueuedWithdrawals` and `sharesToUnderlyingView`.

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L231-234)
```text
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);
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

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
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

**File:** contracts/LRTUnstakingVault.sol (L153-156)
```text
        if (_maxUncompletedWithdrawalCount > 80) {
            revert MaxUncompletedWithdrawalCountTooHigh();
        }
        maxUncompletedWithdrawalCount = _maxUncompletedWithdrawalCount;
```
