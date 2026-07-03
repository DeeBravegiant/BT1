Audit Report

## Title
Auto-Pause Circuit Breaker Locks Initiated Withdrawals With No Cancel Path — (File: `contracts/LRTWithdrawalManager.sol`, `contracts/LRTOracle.sol`)

## Summary
When `LRTOracle._updateRsETHPrice()` detects a price drop beyond `pricePercentageLimit`, it automatically pauses `LRTWithdrawalManager` via a permissionless public call. Users who have already called `initiateWithdrawal` — transferring their rsETH into the contract — have no `cancelWithdrawal` mechanism and cannot recover their tokens for the duration of the pause. The pause has no on-chain time limit, and `completeWithdrawal` is blocked by `whenNotPaused`, constituting a temporary freezing of user funds.

## Finding Description

**Step 1 — User initiates withdrawal, rsETH is transferred to the contract.**

`initiateWithdrawal` (line 166) transfers the user's rsETH to `LRTWithdrawalManager` and records `expectedAssetAmount`:

```solidity
// LRTWithdrawalManager.sol L166-175
IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
assetsCommitted[asset] += expectedAssetAmount;
_addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);
```

At this point the user's rsETH is held by the contract.

**Step 2 — Permissionless auto-pause via `updateRSETHPrice()`.**

`updateRSETHPrice()` is declared `public` (line 87), callable by any address. Inside `_updateRsETHPrice()` (lines 277–281), if the newly computed price falls more than `pricePercentageLimit` below `highestRsethPrice`, the function pauses both `LRTDepositPool` and `LRTWithdrawalManager`, pauses the oracle itself, and returns without updating `rsETHPrice`:

```solidity
// LRTOracle.sol L277-282
if (isPriceDecreaseOffLimit) {
    if (!lrtDepositPool.paused()) lrtDepositPool.pause();
    if (!withdrawalManager.paused()) withdrawalManager.pause();
    _pause();
    return;
}
```

**Step 3 — All user-facing withdrawal functions are blocked; no cancel path exists.**

Both `completeWithdrawal` (line 183) and `unlockQueue` (line 279) carry `whenNotPaused`. There is no `cancelWithdrawal` function anywhere in `LRTWithdrawalManager`. The user's rsETH is irrecoverably locked for the duration of the pause, which has no on-chain expiry and requires a manual admin `unpause()` call.

**Step 4 — Upon unpause, `_calculatePayoutAmount` may settle at a depressed price.**

After the oracle is unpaused, any caller can invoke `updateRSETHPrice()` to update `rsETHPrice` to the current (lower) value. When an operator then calls `unlockQueue`, `_calculatePayoutAmount` (lines 833–834) computes:

```solidity
uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```

If `currentReturn < expectedAssetAmount`, the user's `expectedAssetAmount` is overwritten to the lower value (line 804) while the full `rsETHUnstaked` is burned (line 805). The user receives fewer assets than recorded at initiation time with no ability to have cancelled during the pause window.

## Impact Explanation

**Medium — Temporary freezing of funds.** Users who have called `initiateWithdrawal` have their rsETH locked inside `LRTWithdrawalManager` with no cancel mechanism for the entire duration of the pause. The pause has no on-chain time limit; it persists until an admin manually calls `unpause()`. This directly matches the allowed impact class "Temporary freezing of funds." The secondary consequence — settlement at a depressed price after unpause — compounds the harm but is a direct result of the forced lock-in, not a separate independent vulnerability.

## Likelihood Explanation

The auto-pause trigger is permissionless: any address can call `updateRSETHPrice()` during a market downturn. EigenLayer slashing events, LST de-pegs, or coordinated selling of underlying assets are all realistic triggers that can cause the computed price to breach `pricePercentageLimit`. The pause duration is unbounded (admin-controlled unpause only). The scenario is repeatable across any market stress event and requires no privileged access, no victim mistake, and no external protocol compromise beyond normal market price movement.

## Recommendation

1. **Add a `cancelWithdrawal` function** that allows users to reclaim their rsETH while a request is still in the locked (pre-`unlockQueue`) state. This eliminates the fund-freeze impact entirely.
2. **Introduce a grace period after unpause** before `unlockQueue` can be called, giving users time to cancel pending requests if they no longer wish to withdraw at the current price.
3. **Alternatively, snapshot the rsETH price at `initiateWithdrawal` time** and guarantee payout is calculated at that price, so the pause window cannot worsen the user's settlement rate.

## Proof of Concept

1. rsETH price is 1.05 ETH/rsETH. Alice calls `initiateWithdrawal(ETH, 100e18)`. Her 100 rsETH is transferred to `LRTWithdrawalManager`; `expectedAssetAmount` is recorded as 105 ETH.
2. A market event causes the computed rsETH price to drop to 0.90 ETH/rsETH (exceeding `pricePercentageLimit` below `highestRsethPrice`). Any external caller invokes `updateRSETHPrice()`. `LRTOracle._updateRsETHPrice()` fires `withdrawalManager.pause()` and returns without updating `rsETHPrice`.
3. Alice attempts `completeWithdrawal` → reverts (`whenNotPaused`). She attempts to cancel → no such function exists. Her 100 rsETH is frozen in the contract.
4. After an extended pause, the admin calls `unpause()` on `LRTWithdrawalManager`. The oracle is also unpaused; any caller invokes `updateRSETHPrice()`, updating `rsETHPrice` to 0.90 ETH/rsETH.
5. An operator calls `unlockQueue`. `_calculatePayoutAmount` computes `currentReturn = 100e18 * 0.90e18 / 1e18 = 90 ETH`. Since `90 < 105`, Alice's `expectedAssetAmount` is overwritten to 90 ETH and all 100 rsETH is burned.
6. Alice calls `completeWithdrawal` and receives 90 ETH — 15 ETH less than recorded at initiation — with no ability to have cancelled during the pause.