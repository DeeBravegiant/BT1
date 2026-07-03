All code references check out. The claim is verified against the actual source:

- `getAvailableAssetAmount` at line 599–603 uses saturating subtraction against a live `getTotalAssetDeposits` view. [1](#0-0) 
- `assetsCommitted[asset]` is incremented at line 173 and only decremented inside `_unlockWithdrawalRequests` at line 802. [2](#0-1) [3](#0-2) 
- `getTotalAssetDeposits` sums live EigenLayer balances via `getWithdrawableShare` → `sharesToUnderlyingView`, both of which reflect the current slashing factor. [4](#0-3) 
- The oracle auto-pause only fires above `pricePercentageLimit`; sub-threshold slashing leaves the protocol unpaused and the broken accounting exposed. [5](#0-4) 
- The `initiateWithdrawal` guard at line 170 reverts unconditionally once `getAvailableAssetAmount` returns 0. [6](#0-5) 
- The contract's own NatSpec at line 147–149 acknowledges a slashing edge case but does not address the accounting mismatch. [7](#0-6) 

---

Audit Report

## Title
Stale `assetsCommitted` Accounting After EigenLayer Slashing Temporarily Blocks All New `initiateWithdrawal` Calls - (File: `contracts/LRTWithdrawalManager.sol`)

## Summary
`assetsCommitted[asset]` is a monotonically-increasing accumulator that is only reduced when `_unlockWithdrawalRequests` processes queued requests. `getAvailableAssetAmount` computes availability as `getTotalAssetDeposits(asset) - assetsCommitted[asset]`, where `getTotalAssetDeposits` is a live view that reflects EigenLayer's current withdrawable shares. A slashing event can push `totalAssets` below the already-committed amount, causing `getAvailableAssetAmount` to return 0 and every subsequent `initiateWithdrawal` call to revert with `ExceedAmountToWithdraw`. The freeze persists until an operator calls `unlockQueue`, which can span the full `withdrawalDelayBlocks` window (~8 days).

## Finding Description
**Root cause — static accumulator vs. live view:**

`getAvailableAssetAmount` (line 599–603):
```solidity
function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
    ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
    uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
    availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
}
```

`getTotalAssetDeposits` (line 385–397) sums live on-chain balances including `assetStakedInEigenLayer` (from `NodeDelegatorHelper.getAssetBalance` → `getWithdrawableShare` on EigenLayer's `DelegationManager`) and `assetUnstakingFromEigenLayer`. Both values reflect EigenLayer's current slashing factor and can decrease at any time without any on-chain action by the protocol.

`assetsCommitted` lifecycle:
- Increased at `initiateWithdrawal` line 173: `assetsCommitted[asset] += expectedAssetAmount;`
- Decreased only inside `_unlockWithdrawalRequests` line 802: `assetsCommitted[asset] -= request.expectedAssetAmount;`

There is no mechanism that adjusts `assetsCommitted` downward when `totalAssets` decreases due to slashing. The two values are decoupled after the initial commitment.

**Exploit flow:**
1. `totalAssets` for an asset = 100 ether (via EigenLayer strategy).
2. Users call `initiateWithdrawal` until `assetsCommitted[asset]` = 95 ether (each call passes: `expectedAmount <= 100 - committed`).
3. EigenLayer slashing reduces `getWithdrawableShare` by 10% → `getTotalAssetDeposits` now returns 90 ether.
4. `getAvailableAssetAmount` returns 0 (saturating subtraction: `90 < 95`).
5. Every subsequent `initiateWithdrawal` call reverts with `ExceedAmountToWithdraw`, regardless of requested amount.
6. Freeze persists until operator calls `unlockQueue`, reducing `assetsCommitted` below the new `totalAssets`.

**Why existing guards fail:**
The `LRTOracle` auto-pause (line 277–282) only fires when the price drop exceeds `pricePercentageLimit`. For slashing events below that threshold, the protocol remains unpaused and the broken accounting is fully exposed. The protocol's own NatSpec at line 147–149 acknowledges a slashing edge case but provides no accounting correction.

## Impact Explanation
**Medium — Temporary freezing of funds.** All new withdrawal initiations for the affected asset are blocked. Users holding rsETH cannot queue a withdrawal through the normal path. The freeze is temporary but operator-dependent: it persists until `unlockQueue` is called, which can span the full `withdrawalDelayBlocks` window (~57,600 blocks / ~8 days by default, as set at line 94). This exactly matches the allowed impact of "Temporary freezing of funds."

## Likelihood Explanation
EigenLayer's slashing mechanism is live and can reduce `getWithdrawableShare` for any strategy. The protocol explicitly integrates with EigenLayer across multiple `NodeDelegator` contracts and uses live EigenLayer data in its core accounting. A slashing event that reduces `totalAssets` by even a small percentage (e.g., 5–10%) while `assetsCommitted` is near the previous `totalAssets` ceiling is sufficient to trigger the freeze. No privileged access or attacker action is required — the condition arises from the protocol's own accounting design interacting with a designed feature of EigenLayer. The freeze is repeatable across any slashing event.

## Recommendation
1. **Clamp `assetsCommitted` inside `getAvailableAssetAmount`**: if `assetsCommitted[asset] > totalAssets`, treat the effective committed amount as `totalAssets` (available = 0) without allowing the underflow to propagate into future accounting.
2. **Add a reconciliation step in `_unlockWithdrawalRequests`**: if `assetsCommitted[asset] > totalAssets` at the start of queue processing, cap `assetsCommitted[asset]` to `totalAssets` so the accounting self-heals on the next `unlockQueue` call.
3. **Alternatively**, track `assetsCommitted` as a share of `totalAssets` at commitment time rather than an absolute amount, so it scales proportionally with slashing.

## Proof of Concept
```solidity
// Foundry fork test outline:
// 1. Fork mainnet with EigenLayer deployed.
// 2. Deposit ETH through LRTDepositPool until totalAssets = 100 ether.
// 3. Call initiateWithdrawal in a loop until assetsCommitted[ETH] = 95 ether.
//    Assert: each call succeeds (expectedAmount <= getAvailableAssetAmount).
// 4. Simulate EigenLayer slashing: reduce the strategy's slashing factor by 10%
//    (e.g., via vm.mockCall on DelegationManager.getWithdrawableShares to return 90% of prior value).
//    Assert: getTotalAssetDeposits(ETH) == 90 ether.
//    Assert: getAvailableAssetAmount(ETH) == 0.
// 5. Call initiateWithdrawal(ETH, minAmount, "") with any amount.
//    Assert: reverts with ExceedAmountToWithdraw.
// 6. Operator calls unlockQueue(ETH, ...) to process existing requests.
//    Assert: assetsCommitted[ETH] decreases below 90 ether.
//    Assert: getAvailableAssetAmount(ETH) > 0.
//    Assert: initiateWithdrawal now succeeds.
// The invariant getAvailableAssetAmount(asset) > 0 when totalAssets > 0 and rsETH supply > 0
// is violated between steps 4 and 6, spanning up to withdrawalDelayBlocks (~8 days).
```

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L147-149)
```text
    /// @dev This function is only callable by the user and is used to initiate a withdrawal request for a specific
    /// asset. Will be finalised by calling `completeWithdrawal` after the manager unlocked the request and the delay
    /// has past. There is an edge case were the user withdraws last underlying asset and that asset gets slashed.
```

**File:** contracts/LRTWithdrawalManager.sol (L170-170)
```text
        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
```

**File:** contracts/LRTWithdrawalManager.sol (L173-173)
```text
        assetsCommitted[asset] += expectedAssetAmount;
```

**File:** contracts/LRTWithdrawalManager.sol (L599-603)
```text
    function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
        availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L802-802)
```text
            assetsCommitted[asset] -= request.expectedAssetAmount;
```

**File:** contracts/NodeDelegatorHelper.sol (L31-39)
```text
    function getAssetBalance(ILRTConfig lrtConfig, address asset) internal view returns (uint256) {
        address strategy = lrtConfig.assetStrategy(asset);
        if (strategy == address(0)) {
            return 0;
        }
        uint256 withdrawableShare = getWithdrawableShare(lrtConfig, IStrategy(strategy));

        return IStrategy(strategy).sharesToUnderlyingView(withdrawableShare);
    }
```

**File:** contracts/LRTOracle.sol (L273-282)
```text
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }
```
