### Title
Publicly Callable `updateRSETHPrice()` Contains Deeply Nested Unbounded Loop Over EigenLayer Queued Withdrawals, Risking Gas Exhaustion - (File: contracts/LRTOracle.sol, contracts/NodeDelegator.sol)

### Summary
`LRTOracle.updateRSETHPrice()` is publicly callable and internally executes a deeply nested loop: for each supported asset, for each NDC, it calls `NodeDelegator.getAssetUnstaking()`, which in turn calls EigenLayer's `getQueuedWithdrawals()` and iterates over every queued withdrawal and every strategy within each withdrawal. The total gas cost scales as O(N × M × K × S) — assets × NDCs × queued withdrawals per NDC × strategies per withdrawal — and `getQueuedWithdrawals` is called redundantly N times per NDC (once per asset) even though the result is identical for each asset. The protocol's own comment acknowledges that at 120 total uncompleted withdrawals the function runs out of gas.

### Finding Description
The call chain is:

1. `LRTOracle.updateRSETHPrice()` (public, no role check) → `_updateRsETHPrice()` → `_getTotalEthInProtocol()`
2. `_getTotalEthInProtocol()` loops over every supported asset and calls `ILRTDepositPool.getTotalAssetDeposits(asset)` → `getAssetDistributionData(asset)`
3. `getAssetDistributionData()` loops over every NDC in `nodeDelegatorQueue` and calls `INodeDelegator.getAssetUnstaking(asset)` for each
4. `NodeDelegator.getAssetUnstaking()` calls `_getDelegationManager().getQueuedWithdrawals(address(this))` — an external call to EigenLayer — and then runs a **nested loop** over every returned withdrawal and every strategy within it

Because step 3 calls `getAssetUnstaking` once per asset per NDC, `getQueuedWithdrawals` is invoked N × M times total (e.g., 3 assets × 10 NDCs = 30 external calls), even though the withdrawal data for a given NDC is identical regardless of which asset is being queried. Each of those 30 calls returns up to K withdrawals, each with S strategies, producing up to N × M × K × S inner-loop iterations plus the memory allocation cost of deserializing the full withdrawal structs from EigenLayer.

The protocol's own inline comment in `LRTUnstakingVault.setMaxUncompletedWithdrawalCount` reads:

> "120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price"

and caps `maxUncompletedWithdrawalCount` at 80 as a safety margin. This confirms the team is aware that the gas cost of `updateRSETHPrice()` grows with the withdrawal count and that the function becomes uncallable above a threshold. However, the cap is a global count across all NDCs, not a per-NDC cap, so the distribution of withdrawals across NDCs affects the actual gas cost. Additionally, the redundant N-fold repetition of `getQueuedWithdrawals` per NDC means the effective gas budget is consumed N times faster than the comment assumes. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

### Impact Explanation
If `updateRSETHPrice()` reverts due to gas exhaustion, the stored `rsETHPrice` in `LRTOracle` becomes permanently stale. Every subsequent deposit (`getRsETHAmountToMint` divides by `lrtOracle.rsETHPrice()`) and every withdrawal unlock (`_calculatePayoutAmount` uses `rsETHPrice`) will use the stale rate, causing users to receive incorrect rsETH amounts or incorrect asset payouts. The protocol's fee-minting mechanism also stops functioning. Because `updateRSETHPrice()` is the only non-admin path to refresh the price, a gas-exhaustion condition effectively freezes correct protocol operation for all users.

**Impact: Medium — Unbounded gas consumption / Temporary freezing of funds.**

### Likelihood Explanation
The protocol currently caps `maxUncompletedWithdrawalCount` at 80 and `maxNodeDelegatorLimit` at 10, with typically 2–3 supported assets. Under these parameters the function is callable, but the margin is narrow: the team's own comment places the failure threshold at 120 withdrawals. Any of the following realistic scenarios closes that margin:

- The number of supported assets grows (each new asset multiplies the number of `getQueuedWithdrawals` calls by one more)
- Forced operator undelegations (acknowledged in the comment: "ndc count × asset count = 15") push the withdrawal count toward the cap
- EigenLayer's `getQueuedWithdrawals` return value grows in memory size due to protocol upgrades

No attacker action is required; the condition can arise from normal protocol growth or operator events.

### Recommendation
- **Short term:** Cache the result of `getQueuedWithdrawals(ndc)` once per NDC and reuse it across all asset iterations, reducing the external call count from N × M to M and the inner-loop iterations by a factor of N.
- **Long term:** Refactor `getAssetUnstaking` to accept a pre-fetched withdrawal list, or aggregate all asset unstaking amounts in a single pass per NDC. Consider adding an explicit gas-cost simulation test that fails if `updateRSETHPrice()` exceeds a safe gas threshold under maximum protocol parameters.

### Proof of Concept
With 3 supported assets, 10 NDCs, and 80 total queued withdrawals (8 per NDC, 2 strategies each):

```
_getTotalEthInProtocol():
  for each of 3 assets:
    getTotalAssetDeposits(asset):
      getAssetDistributionData(asset):
        for each of 10 NDCs:
          getAssetUnstaking(asset):
            getQueuedWithdrawals(ndc)   ← external call (×30 total)
            for each of 8 withdrawals:
              for each of 2 strategies: ← 16 inner iterations per NDC per asset
```

Total: 30 external calls to EigenLayer + 3 × 10 × 8 × 2 = 480 inner iterations, plus memory allocation for 30 full withdrawal struct arrays. Adding more assets (e.g., 5) raises the external call count to 50 and inner iterations to 800, pushing toward the acknowledged gas limit. The function `updateRSETHPrice()` at line 87 of `LRTOracle.sol` carries no access control and can be called by any address, making this condition triggerable by normal protocol usage rather than a targeted attack. [1](#0-0) [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** contracts/LRTUnstakingVault.sol (L150-158)
```text
    function setMaxUncompletedWithdrawalCount(uint256 _maxUncompletedWithdrawalCount) external onlyLRTManager {
        // 120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price
        // Need buffer for theoretical operator forced undelegations (ndc count * asset count = 15)
        if (_maxUncompletedWithdrawalCount > 80) {
            revert MaxUncompletedWithdrawalCountTooHigh();
        }
        maxUncompletedWithdrawalCount = _maxUncompletedWithdrawalCount;

        emit MaxUncompletedWithdrawalCountSet(_maxUncompletedWithdrawalCount);
```
