### Title
Unbounded Gas Consumption in `updateRSETHPrice()` via Redundant `getQueuedWithdrawals` Calls Across All Assets Per NDC - (File: contracts/NodeDelegator.sol, contracts/LRTOracle.sol)

### Summary
`LRTOracle.updateRSETHPrice()` is publicly callable and internally invokes `_getTotalEthInProtocol()`, which iterates over every supported asset and for each asset iterates over every NodeDelegator (NDC). For each (asset, NDC) pair, `NodeDelegator.getAssetUnstaking(asset)` is called, which in turn calls EigenLayer's `DelegationManager.getQueuedWithdrawals(address(this))` and iterates over all returned withdrawal structs. This means `getQueuedWithdrawals` is called **M × N** times (M = supported assets, N = NDCs), fetching the same per-NDC withdrawal data redundantly M times. As the protocol adds more supported assets or NDCs, the gas cost of `updateRSETHPrice()` grows proportionally with no hard ceiling that guarantees it stays within the block gas limit.

### Finding Description
The call chain is:

```
updateRSETHPrice() [public, no access control]
  → _updateRsETHPrice()
  → _getTotalEthInProtocol()                    // LRTOracle.sol:336
      for each asset (M assets):
        → getTotalAssetDeposits(asset)           // LRTDepositPool.sol:385
          → getAssetDistributionData(asset)      // LRTDepositPool.sol:447
              for each NDC (N NDCs):
                → getAssetUnstaking(asset)       // NodeDelegator.sol:405
                    → getQueuedWithdrawals(this)  // EigenLayer external call
                    → nested loop over all withdrawals × strategies
```

`getQueuedWithdrawals` is an external call to EigenLayer that reads all queued withdrawal structs for the NDC from storage. It is called **M × N** times total — once per (asset, NDC) pair — even though the result is identical for all M assets queried against the same NDC. The protocol caps `maxUncompletedWithdrawalCount` at 80 (bounded W), but M and N have no hard upper bounds enforced in the contract code: `maxNodeDelegatorLimit` is freely updatable by admin, and new supported assets can be added via `TIME_LOCK_ROLE`.

Even at current protocol scale (M=3, N=10), this produces 30 external calls to EigenLayer per `updateRSETHPrice()` invocation, each reading and deserializing up to 80 withdrawal structs. As M and N grow, the gas cost scales as O(M × N × W), and there is no pre-emptive gas check to prevent the function from reverting mid-execution.

### Impact Explanation
If `updateRSETHPrice()` exceeds the block gas limit, the rsETH price oracle becomes permanently frozen. The stored `rsETHPrice` is used directly in `getRsETHAmountToMint()` for all deposits and in `LRTWithdrawalManager` for withdrawal unlock calculations. A frozen oracle means:
- Deposits continue at a stale price, enabling arbitrage that drains protocol value.
- Withdrawal unlocking (`unlockQueue`) also calls `updateRSETHPrice()` internally, so the withdrawal queue becomes permanently stuck — a permanent freeze of user funds.

**Impact: Medium — Unbounded gas consumption / Temporary-to-permanent freezing of funds.**

### Likelihood Explanation
`updateRSETHPrice()` is publicly callable with no access control beyond `whenNotPaused`. Any user can trigger it. The gas cost grows deterministically as the protocol adds assets and NDCs. The protocol's own comment in `LRTUnstakingVault.sol` acknowledges the gas sensitivity: *"120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price."* This confirms the team is aware the function is near its gas ceiling even today. Adding one more supported asset or a few more NDCs (both routine governance actions) can push it over.

### Recommendation
Refactor `getAssetUnstaking` out of the per-asset loop. Instead of calling `getQueuedWithdrawals` once per (asset, NDC) pair, call it once per NDC and accumulate balances for all assets in a single pass. This reduces `getQueuedWithdrawals` calls from M × N to N. Additionally, enforce a hard upper bound on `maxNodeDelegatorLimit` and the number of supported assets to guarantee the gas cost of `updateRSETHPrice()` stays within a safe margin of the block gas limit.

### Proof of Concept
1. `updateRSETHPrice()` is called (by anyone).
2. `_getTotalEthInProtocol()` iterates over M=3 supported assets.
3. For each asset, `getAssetDistributionData(asset)` iterates over N=10 NDCs.
4. For each NDC, `getAssetUnstaking(asset)` calls `getQueuedWithdrawals(ndc)` on EigenLayer — 30 external storage-reading calls total.
5. Each call deserializes and iterates over all queued withdrawal structs for that NDC.
6. If a governance action adds a 4th asset (M=4) and 5 more NDCs (N=15), the call count becomes 60, doubling gas cost with no contract-level protection.
7. Once gas exceeds the block limit, `updateRSETHPrice()` reverts on every call, freezing the oracle and blocking `unlockQueue` in `LRTWithdrawalManager`, permanently trapping user withdrawal requests. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** contracts/LRTUnstakingVault.sol (L151-156)
```text
        // 120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price
        // Need buffer for theoretical operator forced undelegations (ndc count * asset count = 15)
        if (_maxUncompletedWithdrawalCount > 80) {
            revert MaxUncompletedWithdrawalCountTooHigh();
        }
        maxUncompletedWithdrawalCount = _maxUncompletedWithdrawalCount;
```
