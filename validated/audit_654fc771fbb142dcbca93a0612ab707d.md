Audit Report

## Title
`initiateWithdrawal` Availability Guard Uses Aggregate TVL While `unlockQueue` Draws Only From Unstaking Vault — (`File: contracts/LRTWithdrawalManager.sol`)

## Summary

`initiateWithdrawal` gates new withdrawal requests using `getAvailableAssetAmount`, which calls `LRTDepositPool.getTotalAssetDeposits` and sums assets across all protocol locations including illiquid EigenLayer-staked and EigenLayer-unstaking amounts. However, `unlockQueue` — the only function that marks requests as unlockable — exclusively draws from `unstakingVault.balanceOf(asset)`. Because the protocol's assets routinely reside in EigenLayer strategies, the guard in `initiateWithdrawal` is systematically over-optimistic, allowing users to lock their rsETH into the withdrawal manager for amounts that cannot be serviced until operators complete a multi-step, time-delayed EigenLayer unstaking cycle. No cancel mechanism exists.

## Finding Description

**Root cause — mismatched liquidity scopes between the guard and the unlock path.**

`initiateWithdrawal` transfers the user's rsETH to the contract and then checks:

```solidity
// LRTWithdrawalManager.sol L166-173
IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
assetsCommitted[asset] += expectedAssetAmount;
```

`getAvailableAssetAmount` delegates to `getTotalAssetDeposits`:

```solidity
// LRTWithdrawalManager.sol L599-603
function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
    ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
    uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
    availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
}
```

`getTotalAssetDeposits` sums six buckets, explicitly including `assetStakedInEigenLayer` and `assetUnstakingFromEigenLayer`:

```solidity
// LRTDepositPool.sol L385-397
uint256 effectiveAssetWithEigenLayer = assetStakedInEigenLayer + assetUnstakingFromEigenLayer;
return (assetLyingInDepositPool + assetLyingInNDCs + effectiveAssetWithEigenLayer + assetLyingInConverter
        + assetLyingUnstakingVault);
```

These EigenLayer amounts are populated by iterating all NodeDelegators:

```solidity
// LRTDepositPool.sol L446-456
assetStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetBalance(asset);
assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);
```

In contrast, `_createUnlockParams` — called exclusively by `unlockQueue` — sets `totalAvailableAssets` to only the unstaking vault balance:

```solidity
// LRTWithdrawalManager.sol L837-851
return UnlockParams({
    rsETHPrice: lrtOracle.rsETHPrice(),
    assetPrice: lrtOracle.getAssetPrice(asset),
    totalAvailableAssets: unstakingVault.balanceOf(asset)
});
```

`unlockQueue` then passes this single-provider figure into `_unlockWithdrawalRequests` and redeems only from the vault:

```solidity
// LRTWithdrawalManager.sol L301-307
(rsETHBurned, assetAmountUnlocked) = _unlockWithdrawalRequests(
    asset, params.totalAvailableAssets, params.rsETHPrice, params.assetPrice, firstExcludedIndex
);
if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
unstakingVault.redeem(asset, assetAmountUnlocked);
```

The user's rsETH is transferred to the contract at initiation and is not returned until `completeWithdrawal` succeeds. rsETH is only burned during `unlockQueue`. There is no `cancelWithdrawal` function anywhere in the contract. The user has no recourse once `initiateWithdrawal` is called.

**Exploit flow:**

1. Protocol holds 1,000 ETH total: 950 ETH staked in EigenLayer via NodeDelegators, 50 ETH in the unstaking vault.
2. `getAvailableAssetAmount(ETH)` returns `1,000 − 0 = 1,000 ETH`.
3. Alice calls `initiateWithdrawal(ETH, rsETHFor900ETH, ...)`. The guard passes (900 < 1,000). Alice's rsETH is transferred to `LRTWithdrawalManager`. `assetsCommitted[ETH] = 900 ETH`.
4. Operator calls `unlockQueue`. `_createUnlockParams` sets `totalAvailableAssets = unstakingVault.balanceOf(ETH) = 50 ETH`. Only 50 ETH worth of requests can be unlocked; Alice's 900 ETH request cannot be processed.
5. To service Alice, the operator must call `NodeDelegator.initiateUnstaking`, wait ≥7 days for EigenLayer's delay, call `NodeDelegator.completeUnstaking`, then call `unlockQueue` again.
6. Alice's rsETH remains locked in `LRTWithdrawalManager` for the entire duration with no way to cancel or reclaim it.

## Impact Explanation

A depositor who calls `initiateWithdrawal` when most protocol assets are in EigenLayer will have their rsETH locked in `LRTWithdrawalManager` for at least the EigenLayer withdrawal delay (≥7 days) plus operator latency, even though the `getAvailableAssetAmount` guard reported sufficient availability. This constitutes **temporary freezing of user funds** (Medium), matching the allowed impact scope. The user cannot cancel, cannot reclaim rsETH, and cannot complete the withdrawal until operators execute the full EigenLayer unstaking cycle and call `unlockQueue` with sufficient vault balance.

## Likelihood Explanation

The normal steady-state of the protocol is that assets are restaked in EigenLayer strategies — that is the protocol's core purpose. Therefore the mismatch between the multi-provider availability check and the single-provider unlock path is triggered on virtually every withdrawal request, not just in edge cases. Any unprivileged user can call `initiateWithdrawal` at any time, and no special conditions are required beyond the protocol operating normally.

## Recommendation

- **Short term:** Replace the `getAvailableAssetAmount` guard in `initiateWithdrawal` with a check against `unstakingVault.balanceOf(asset)` (minus already-committed amounts) so that the guard reflects what `unlockQueue` can actually service immediately.
- **Long term:** Introduce a unified "available-for-withdrawal" accounting layer that tracks only assets that have already reached the unstaking vault, and enforce that `assetsCommitted` never exceeds this figure. Alternatively, add a `cancelWithdrawal` function so users can reclaim their rsETH if their request cannot be serviced within a reasonable time window.

## Proof of Concept

Foundry fork test outline:

```solidity
function test_temporaryFreeze() public {
    // Fork mainnet with 950 ETH in EigenLayer, 50 ETH in unstaking vault
    // Alice holds rsETH equivalent to 900 ETH
    uint256 rsETHAmount = ...; // rsETH for 900 ETH

    // Step 1: getAvailableAssetAmount returns 1000 ETH (passes guard)
    assertGt(withdrawalManager.getAvailableAssetAmount(ETH), 900 ether);

    // Step 2: Alice initiates withdrawal — rsETH is locked
    vm.prank(alice);
    withdrawalManager.initiateWithdrawal(ETH, rsETHAmount, "");
    assertEq(rsETH.balanceOf(alice), 0); // rsETH gone
    assertEq(rsETH.balanceOf(address(withdrawalManager)), rsETHAmount); // locked

    // Step 3: Operator calls unlockQueue — only 50 ETH available, Alice's request skipped
    vm.prank(operator);
    (uint256 burned, uint256 unlocked) = withdrawalManager.unlockQueue(ETH, type(uint256).max, ...);
    assertLt(unlocked, 900 ether); // Alice's request not unlocked

    // Step 4: Alice cannot complete withdrawal — WithdrawalLocked revert
    vm.prank(alice);
    vm.expectRevert(WithdrawalLocked.selector);
    withdrawalManager.completeWithdrawal(ETH, "");

    // Alice's rsETH remains locked until operators complete EigenLayer unstaking cycle
}
```