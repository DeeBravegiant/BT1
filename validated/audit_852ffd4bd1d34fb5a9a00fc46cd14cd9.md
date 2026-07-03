The code is confirmed. Let me verify the exact line numbers and the `LRTUnstakingVault` for completeness.

Audit Report

## Title
Multiplicatively Redundant `getQueuedWithdrawals()` Calls in `updateRSETHPrice()` Cause Unbounded Gas Growth — (`contracts/LRTOracle.sol`, `contracts/LRTDepositPool.sol`, `contracts/NodeDelegator.sol`)

## Summary

`LRTOracle.updateRSETHPrice()` is publicly callable and triggers a call chain that invokes `NodeDelegator.getAssetUnstaking(asset)` once per (supported asset, NDC) pair. Inside `getAssetUnstaking()`, `_getDelegationManager().getQueuedWithdrawals(address(this))` is called on every invocation, meaning the same EigenLayer external call is repeated N times per NDC (once per supported asset) even though the result is identical for a given NDC regardless of which asset is queried. The total external call count scales as O(N×M) for `getQueuedWithdrawals()` and O(N×M×W×S) for `sharesToUnderlyingView()`, where N=supported assets, M=NDCs, W=queued withdrawals per NDC, S=strategies per withdrawal. At realistic protocol scale this pushes `updateRSETHPrice()` past the block gas limit, making it permanently uncallable.

## Finding Description

The confirmed call chain is:

```
updateRSETHPrice()                    [LRTOracle.sol:87]  — public, whenNotPaused only
  _updateRsETHPrice()                 [LRTOracle.sol:214]
    _getTotalEthInProtocol()          [LRTOracle.sol:331]
      for each asset (N):
        getTotalAssetDeposits(asset)  [LRTDepositPool.sol:385]
          getAssetDistributionData()  [LRTDepositPool.sol:426]
            for each NDC (M):
              getAssetUnstaking(asset)[NodeDelegator.sol:405]
                getQueuedWithdrawals(address(this))  ← external call, repeated N×M times
                for each withdrawal (W):
                  for each strategy (S):
                    sharesToUnderlyingView()          ← external call, repeated N×M×W×S times
```

**Step 1 — outer asset loop:** `_getTotalEthInProtocol()` iterates over every supported asset and calls `getTotalAssetDeposits(asset)` for each. [1](#0-0) 

**Step 2 — NDC loop per asset:** `getAssetDistributionData()` iterates over every NDC and calls `getAssetUnstaking(asset)` per NDC. [2](#0-1) 

**Step 3 — redundant external call per (asset, NDC):** `getAssetUnstaking()` unconditionally calls `getQueuedWithdrawals(address(this))` at the top of the function, then iterates over every withdrawal and every strategy within it. [3](#0-2) 

The root cause is that `getQueuedWithdrawals(address(this))` returns data scoped to the NDC, not to any specific asset. Its result is identical for all N asset queries against the same NDC, yet it is called N times per NDC. There is no caching, no deduplication, and no guard preventing this redundant fan-out.

Existing bounds do not prevent the issue:
- `maxNodeDelegatorLimit` defaults to 10 and is admin-increasable via `updateMaxNodeDelegatorLimit()`. [4](#0-3) 
- `maxUncompletedWithdrawalCount` is admin-settable and bounds total withdrawals globally, but even at moderate values the multiplicative factor N amplifies the call count. [5](#0-4) 
- `updateRSETHPrice()` has no gas guard, no call-count cap, and no access control beyond `whenNotPaused`. [6](#0-5) 

## Impact Explanation

**Medium — Unbounded gas consumption.** When `updateRSETHPrice()` exceeds the block gas limit it becomes permanently uncallable, freezing the rsETH/ETH exchange rate. All subsequent deposits will be priced against a stale rate, and the protocol's core accounting invariant (TVL-backed rsETH price) can no longer be maintained. This matches the allowed impact "Medium. Unbounded gas consumption."

## Likelihood Explanation

The gas cost grows with entirely normal, non-adversarial protocol operation: adding supported LSTs (admin), adding NDCs (admin), and operators calling `initiateUnstaking()` / `undelegate()`. No attacker action is required — the function degrades automatically as the protocol scales. With 5 supported assets, 10 NDCs, and 20 queued withdrawals per NDC (2 strategies each), the function already makes 50 `getQueuedWithdrawals()` calls and 200 `sharesToUnderlyingView()` calls per invocation. Each `getQueuedWithdrawals()` call loads and returns all withdrawal structs for an NDC, making it significantly more expensive than a simple storage read. Any unprivileged caller can trigger the condition by calling the public `updateRSETHPrice()`.

## Recommendation

1. **Fetch `getQueuedWithdrawals()` once per NDC, not once per (asset, NDC) pair.** Restructure `getAssetDistributionData()` to collect all withdrawal data for each NDC in a single pass, then compute per-asset unstaking amounts from the cached result.
2. **Alternatively, add a `getAssetUnstakingBatch(address[] assets)` function** to `NodeDelegator` that calls `getQueuedWithdrawals()` once and returns amounts for all assets in a single call, replacing the N individual `getAssetUnstaking(asset)` calls.
3. **Add a gas guard** or enforce a maximum on `supportedAssets.length × nodeDelegatorQueue.length × maxUncompletedWithdrawalCount` to ensure `updateRSETHPrice()` remains callable within block gas limits.

## Proof of Concept

Deploy on a mainnet fork with:
- 5 supported assets (stETH, rETH, cbETH, swETH, ETH_TOKEN)
- 10 NDCs each delegated to an EigenLayer operator
- Each NDC with 10 queued withdrawals (2 strategies each) via `initiateUnstaking()`

Call `updateRSETHPrice()` and measure gas. Observe:
- `getQueuedWithdrawals()` is called 50 times (5 assets × 10 NDCs)
- `sharesToUnderlyingView()` is called 100 times (5 × 10 × 2 × 1 withdrawal per call)

Increase `maxUncompletedWithdrawalCount` and add more withdrawals per NDC. Observe gas growing as O(N×M×W×S) until the transaction reverts with out-of-gas. The entry point is fully permissionless — no privileged role is needed to call `updateRSETHPrice()`. [6](#0-5)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L336-341)
```text
        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
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

**File:** contracts/LRTUnstakingVault.sol (L39-40)
```text
    uint256 public uncompletedWithdrawalCount;
    uint256 public maxUncompletedWithdrawalCount;
```
