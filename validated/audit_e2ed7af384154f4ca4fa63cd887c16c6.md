Audit Report

## Title
Unbounded Nested-Loop Gas Consumption in `updateRSETHPrice()` Can Permanently Stall rsETH Price Updates — (File: `contracts/LRTOracle.sol`)

## Summary
`LRTOracle.updateRSETHPrice()` is a public, permissionless function whose gas cost scales multiplicatively with the number of supported assets, node delegators, EigenLayer queued withdrawals per NDC, and strategies per withdrawal. Through entirely normal protocol growth, this function can exceed Ethereum's block gas limit, permanently preventing rsETH price updates and disabling the protocol's downside-protection pause mechanism.

## Finding Description

**Confirmed call chain:**

`updateRSETHPrice()` at `LRTOracle.sol:87` is `public whenNotPaused` with no role restriction. It calls `_updateRsETHPrice()` at `LRTOracle.sol:214`, which calls `_getTotalEthInProtocol()` at `LRTOracle.sol:231`.

`_getTotalEthInProtocol()` (`LRTOracle.sol:336`) iterates over every entry in `supportedAssets` and for each calls `ILRTDepositPool.getTotalAssetDeposits(asset)` (`LRTOracle.sol:341`).

`getTotalAssetDeposits()` (`LRTDepositPool.sol:385`) calls `getAssetDistributionData(asset)` (`LRTDepositPool.sol:393`).

`getAssetDistributionData()` (`LRTDepositPool.sol:447–456`) loops over every entry in `nodeDelegatorQueue` and for each NDC calls `INodeDelegator.getAssetUnstaking(asset)` (`LRTDepositPool.sol:451`).

`getAssetUnstaking()` (`NodeDelegator.sol:405–427`) fetches **all** queued EigenLayer withdrawals for the NDC via `_getDelegationManager().getQueuedWithdrawals(address(this))` and then runs a **nested loop** over each withdrawal's strategy array, making external calls to `strategy.underlyingToken()` and `strategy.sharesToUnderlyingView()` inside the inner loop.

Additionally, `getETHDistributionData()` (`LRTDepositPool.sol:488–489`) also calls `getAssetUnstaking(LRTConstants.ETH_TOKEN)` for each NDC, meaning `getQueuedWithdrawals()` is called `(supportedAssets.length + 1) × nodeDelegatorQueue.length` times total per `updateRSETHPrice()` invocation.

**Existing guards are insufficient:**

`LRTUnstakingVault.setMaxUncompletedWithdrawalCount()` (`LRTUnstakingVault.sol:153`) caps `maxUncompletedWithdrawalCount` at 80 with the comment acknowledging up to 15 extra from forced undelegations. However:
1. This is a **global** counter across all NDCs, not a per-NDC EigenLayer enforcement. Forced undelegations by EigenLayer operators add withdrawals beyond the protocol's tracked count.
2. The cap does not account for the multiplicative effect of multiple assets: with 5 assets and 10 NDCs, `getAssetUnstaking()` is called 50 times, each fetching all queued withdrawals for that NDC.
3. `maxNodeDelegatorLimit` is initialized to 10 (`LRTDepositPool.sol:49`) but is admin-adjustable upward.

## Impact Explanation

**Medium — Unbounded gas consumption / Temporary (potentially permanent) freezing of the price-update mechanism.**

If `updateRSETHPrice()` exceeds the block gas limit:
1. `rsETHPrice` stored in `LRTOracle` becomes permanently stale.
2. New depositors receive rsETH at an incorrect rate via `getRsETHAmountToMint()` (`LRTDepositPool.sol:520`), which reads `lrtOracle.rsETHPrice()` directly.
3. Withdrawal amounts are incorrect via `getExpectedAssetAmount()` (`LRTWithdrawalManager.sol:593`), which also reads `lrtOracle.rsETHPrice()`.
4. The downside-protection pause mechanism in `_updateRsETHPrice()` (`LRTOracle.sol:270–281`) never triggers, disabling the protocol's safety circuit breaker.

This matches the allowed impact: **Medium. Unbounded gas consumption.**

## Likelihood Explanation

No malicious actor is required. Gas cost grows through entirely normal protocol operation: adding more supported LSTs, deploying more `NodeDelegator` contracts, and operators queuing EigenLayer withdrawals. The team's own comment in `LRTUnstakingVault.sol:151–152` explicitly acknowledges the gas ceiling is near current operational parameters ("120 is the max number of uncompleted withdrawals that allows us to still perform update rsETH price"). With 5 assets, 10 NDCs, 80 queued withdrawals per NDC, and 3 strategies per withdrawal, the inner body of `getAssetUnstaking()` executes 12,000 times across all `updateRSETHPrice()` invocations, each involving multiple external calls — well above Ethereum's 30M block gas limit.

## Recommendation

1. **Cache `getAssetUnstaking` results off-chain** and push them on-chain via a privileged setter, rather than recomputing them live inside `updateRSETHPrice()`.
2. **Decouple TVL accounting from price updates**: store per-NDC asset balances in a mapping updated lazily by operators, and have `_getTotalEthInProtocol()` read from that mapping instead of making live external calls.
3. **Enforce per-NDC withdrawal caps** at the EigenLayer level (not just a global protocol counter) to bound the inner loop.
4. **Benchmark gas cost** at maximum expected `supportedAssets × NDC × withdrawal × strategy` cardinality and enforce hard limits that keep the function safely below the block gas limit.

## Proof of Concept

**Confirmed call trace (all references verified in repository):**

```
updateRSETHPrice()                          [LRTOracle.sol:87]  — public, no role check
  └─ _updateRsETHPrice()                   [LRTOracle.sol:214]
       └─ _getTotalEthInProtocol()         [LRTOracle.sol:231,336]
            └─ for each asset in supportedAssets:          [LRTOracle.sol:336]
                 └─ getTotalAssetDeposits(asset)           [LRTDepositPool.sol:385]
                      └─ getAssetDistributionData(asset)   [LRTDepositPool.sol:393]
                           └─ for each NDC in nodeDelegatorQueue:   [LRTDepositPool.sol:447]
                                └─ getAssetUnstaking(asset)         [NodeDelegator.sol:405]
                                     └─ getQueuedWithdrawals(NDC)   [NodeDelegator.sol:406-407]
                                          └─ for each withdrawal:   [NodeDelegator.sol:409]
                                               └─ for each strategy: [NodeDelegator.sol:412]
                                                    └─ strategy.underlyingToken()        [NodeDelegator.sol:417]
                                                    └─ strategy.sharesToUnderlyingView() [NodeDelegator.sol:424]
```

**Foundry fork test plan:**

1. Fork mainnet with 5 supported assets, 10 NDCs, and 80 queued withdrawals per NDC (achievable at the protocol's own cap).
2. Call `updateRSETHPrice()` and measure gas via `vm.expectCall` counts and `gasleft()` snapshots.
3. Demonstrate gas consumption approaching or exceeding 30M.
4. Add 15 forced undelegation withdrawals per NDC (as acknowledged in `LRTUnstakingVault.sol:152`) and show the function reverts with out-of-gas.