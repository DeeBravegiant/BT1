### Title
Unbounded Gas Consumption in Publicly Callable `updateRSETHPrice()` via Nested Cross-Contract Loops — (File: contracts/LRTOracle.sol)

### Summary
`LRTOracle.updateRSETHPrice()` is a public, permissionless function that internally executes a deeply nested loop: for every supported asset it calls `LRTDepositPool.getTotalAssetDeposits()`, which in turn loops over every NodeDelegator and calls `NodeDelegator.getAssetUnstaking()`, which issues an external call to EigenLayer's `DelegationManager.getQueuedWithdrawals()` and then iterates over every queued withdrawal and every strategy within it. As the protocol scales through normal operator activity, the cumulative gas cost of this call chain can exceed the block gas limit, permanently preventing price updates and causing the rsETH exchange rate to become stale.

### Finding Description

The call chain is:

```
updateRSETHPrice()                          [public, whenNotPaused — LRTOracle.sol:87]
  └─ _getTotalEthInProtocol()               [LRTOracle.sol:331-349]
       └─ for each of N supported assets:
            getTotalAssetDeposits(asset)     [LRTDepositPool.sol:385-397]
              └─ getAssetDistributionData()  [LRTDepositPool.sol:426-462]
                   └─ for each of M NDCs:
                        getAssetUnstaking(asset)  [NodeDelegator.sol:405-427]
                          └─ DelegationManager.getQueuedWithdrawals()  [external call]
                               └─ for each of K withdrawals × L strategies
```

`_getTotalEthInProtocol()` iterates over every supported asset: [1](#0-0) 

For each asset, `getAssetDistributionData()` iterates over every NDC and calls `getAssetUnstaking()` on each: [2](#0-1) 

`getAssetUnstaking()` issues an external call to EigenLayer and then iterates over all returned queued withdrawals and all strategies within each: [3](#0-2) 

The total number of EigenLayer external calls per `updateRSETHPrice()` invocation is **N × M** (assets × NDCs). With 5 supported assets and 10 NDCs, that is 50 external `getQueuedWithdrawals()` calls in a single transaction, each reading unbounded EigenLayer storage.

The protocol acknowledges this scaling concern in a comment in `LRTUnstakingVault.sol`:

> "120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price"

and caps `maxUncompletedWithdrawalCount` at 80. However, this cap is a *total* across all NDCs and is admin-configurable. It does not bound the number of external calls (N × M), nor does it account for growth in the number of supported assets or strategies per withdrawal.

`updateRSETHPrice()` is also called indirectly on the deposit path via `_checkIfDepositAmountExceedesCurrentLimit()` → `getTotalAssetDeposits()`, and on the withdrawal path via `getAvailableAssetAmount()` → `getTotalAssetDeposits()`, meaning the same gas scaling affects user-facing deposit and withdrawal initiation. [4](#0-3) [5](#0-4) 

### Impact Explanation

**Medium — Unbounded gas consumption / Temporary freezing of funds.**

If `updateRSETHPrice()` becomes uncallable due to gas exhaustion:
- The stored `rsETHPrice` becomes permanently stale.
- All subsequent deposits compute `rsethAmountToMint = (amount × assetPrice) / rsETHPrice` using the stale price, causing systematic over- or under-minting of rsETH relative to the true protocol TVL.
- If `getAssetDistributionData()` itself exceeds the block gas limit, `depositETH()`, `depositAsset()`, and `initiateWithdrawal()` all revert, temporarily freezing user access to the protocol.

### Likelihood Explanation

**Low-Medium.** The state that triggers this condition — many supported assets, many NDCs, many queued EigenLayer withdrawals — is reached through entirely normal, non-malicious operator activity. The protocol's own comment acknowledges the gas ceiling exists. As the protocol expands to more LSTs and more NodeDelegators (the admin can raise `maxNodeDelegatorLimit` beyond 10), the risk increases monotonically. No attacker action is required; organic protocol growth is sufficient.

### Recommendation

1. Cache `getQueuedWithdrawals()` results off-chain and push them on-chain via a keeper, rather than re-fetching from EigenLayer on every price update.
2. Decouple the TVL accounting loop from the user-facing deposit/withdrawal path. Store a running `totalAssetDeposits` counter updated incrementally on each deposit/withdrawal/unstaking event, rather than recomputing it by iterating all NDCs on every call.
3. Add an explicit cap on the number of supported assets (analogous to `maxNodeDelegatorLimit`) and enforce that `N × M × maxUncompletedWithdrawalCount` stays within a safe gas budget.
4. Consider splitting `updateRSETHPrice()` into paginated calls so that gas cost per transaction is bounded regardless of protocol size.

### Proof of Concept

1. Protocol has 5 supported assets, 10 NDCs, and 80 total queued EigenLayer withdrawals (8 per NDC), each with 2 strategies.
2. Anyone calls `LRTOracle.updateRSETHPrice()`.
3. `_getTotalEthInProtocol()` iterates 5 assets × 10 NDCs = **50 external calls** to `DelegationManager.getQueuedWithdrawals()`, each reading EigenLayer storage for 8 withdrawals × 2 strategies = 16 iterations.
4. Total storage reads from EigenLayer: 50 × 16 = 800, plus all intermediate LRTDepositPool and NodeDelegator storage reads.
5. As `maxNodeDelegatorLimit` is raised or more assets are added, the gas cost grows proportionally until the transaction reverts with out-of-gas, permanently preventing price updates and blocking deposits/withdrawals.

### Citations

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

**File:** contracts/LRTWithdrawalManager.sol (L599-603)
```text
    function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
        availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
    }
```
