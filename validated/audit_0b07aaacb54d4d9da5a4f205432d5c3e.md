Audit Report

## Title
Polynomial Gas Complexity in `updateRSETHPrice()` via Nested Loops Over Assets × NDCs × Queued Withdrawals × Strategies — (`contracts/LRTOracle.sol`)

## Summary
`LRTOracle.updateRSETHPrice()` is a public, permissionless function whose gas cost scales as O(N × M × W × S) — assets × NodeDelegators × queued EigenLayer withdrawals × strategies per withdrawal — due to `getAssetUnstaking()` being called once per (asset, NDC) pair, with each call fetching and iterating over all queued withdrawals. The protocol's own code acknowledges this scaling concern and caps queued withdrawals at 80, but the cap is insufficient because it does not account for the multiplicative effect of assets and NDCs, and `maxNodeDelegatorLimit` has no hard upper bound.

## Finding Description

`updateRSETHPrice()` is `public whenNotPaused` with no role restriction. [1](#0-0) 

It calls `_updateRsETHPrice()` → `_getTotalEthInProtocol()`, which opens the first loop over all supported assets and calls `getTotalAssetDeposits(asset)` for each. [2](#0-1) 

`getTotalAssetDeposits` delegates to `getAssetDistributionData`, which for non-ETH assets opens a second loop over `nodeDelegatorQueue` and calls `INodeDelegator(ndc).getAssetUnstaking(asset)` for every NDC. [3](#0-2) 

For ETH, `getAssetDistributionData` redirects to `getETHDistributionData()`, which independently loops over the same NDC queue and calls `getAssetUnstaking(ETH_TOKEN)` for each NDC — meaning ETH is not folded into the same pass as LSTs. [4](#0-3) 

`NodeDelegator.getAssetUnstaking` opens a third and fourth nested loop: it calls `DelegationManager.getQueuedWithdrawals(address(this))` to fetch all queued withdrawals, then iterates over every strategy inside each withdrawal, calling `strategy.sharesToUnderlyingView()` for non-ETH strategies. [5](#0-4) 

The combined complexity is **O(N × M × W × S)** external calls and storage reads in a single transaction. `getAssetUnstaking` is invoked N×M times (once per asset per NDC), and each invocation performs W×S iterations plus one `getQueuedWithdrawals` external call.

The only existing guard is `maxUncompletedWithdrawalCount`, capped at 80, with the protocol's own comment acknowledging that 120 is the maximum that still allows `updateRSETHPrice` to succeed. [6](#0-5) 

However, `updateMaxNodeDelegatorLimit` enforces only a lower bound (cannot shrink below current queue length) and has no upper bound, allowing the admin to raise it arbitrarily as the protocol scales. [7](#0-6) 

The cap of 80 withdrawals was calibrated for a fixed number of assets and NDCs. As those grow, the same cap becomes insufficient: 5 assets × 10 NDCs × 80 withdrawals × 5 strategies = 20,000 inner iterations plus 50 `getQueuedWithdrawals` external calls in a single transaction.

## Impact Explanation

If `updateRSETHPrice()` consistently reverts due to out-of-gas, `rsETHPrice` becomes permanently stale. Every deposit uses `rsETHPrice` to compute `rsethAmountToMint`. [8](#0-7) 

A stale price causes all minting and withdrawal accounting to use an incorrect exchange rate, constituting incorrect share/asset accounting for all users. This matches **Medium — Unbounded gas consumption**.

## Likelihood Explanation

`updateRSETHPrice()` is permissionlessly callable by any address. No attacker action is required — the gas cost grows automatically with normal protocol operation: adding more supported assets, adding more NDCs, and queuing more EigenLayer withdrawals are all routine operational activities. The protocol team's own comment in `setMaxUncompletedWithdrawalCount` confirms awareness that the function breaks above ~120 uncompleted withdrawals, and the current cap of 80 provides only a narrow safety margin that shrinks as assets and NDCs are added.

## Recommendation

1. **Eliminate per-asset calls to `getAssetUnstaking`**: Compute unstaking amounts for all assets in a single pass per NDC. Replace the per-asset `getAssetUnstaking(asset)` call with a single `getQueuedWithdrawals` call per NDC that accumulates amounts for all assets simultaneously, reducing the `getQueuedWithdrawals` call count from N×M to M.
2. **Decouple unstaking accounting from live EigenLayer queries**: Maintain an on-chain `assetUnstaking[asset]` accumulator updated at queue/complete time instead of re-deriving it from `getQueuedWithdrawals` on every price update.
3. **Hard-cap `maxNodeDelegatorLimit`**: Add an enforced upper bound in `updateMaxNodeDelegatorLimit` analogous to the cap on `maxUncompletedWithdrawalCount`.
4. **Consolidate ETH and LST NDC loops**: `getETHDistributionData` and `getAssetDistributionData` both loop over the full NDC queue independently; merge into a single pass.

## Proof of Concept

Call sequence triggering the nested loops (no privileges required):

```
updateRSETHPrice()                              // public, no auth
  → _updateRsETHPrice()
    → _getTotalEthInProtocol()
      → for each asset in supportedAssetList:           // loop 1: N iterations
          getTotalAssetDeposits(asset)
            → getAssetDistributionData(asset)
              → for each NDC in nodeDelegatorQueue:     // loop 2: M iterations
                  getAssetUnstaking(asset)
                    → DelegationManager.getQueuedWithdrawals(ndc)  // 1 external call per (asset, NDC)
                    → for each queued withdrawal:        // loop 3: W iterations
                        for each strategy:               // loop 4: S iterations
                            strategy.sharesToUnderlyingView(shares)
```

With 5 supported assets, 10 NDCs, 80 queued withdrawals, and 5 strategies per withdrawal: **50 `getQueuedWithdrawals` external calls** and **20,000 inner strategy iterations** in a single transaction. The protocol's own cap of 80 withdrawals was calibrated without accounting for the N×M multiplier from assets and NDCs, making the effective safe limit far lower than the configured maximum.

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

**File:** contracts/LRTUnstakingVault.sol (L151-158)
```text
        // 120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price
        // Need buffer for theoretical operator forced undelegations (ndc count * asset count = 15)
        if (_maxUncompletedWithdrawalCount > 80) {
            revert MaxUncompletedWithdrawalCountTooHigh();
        }
        maxUncompletedWithdrawalCount = _maxUncompletedWithdrawalCount;

        emit MaxUncompletedWithdrawalCountSet(_maxUncompletedWithdrawalCount);
```
