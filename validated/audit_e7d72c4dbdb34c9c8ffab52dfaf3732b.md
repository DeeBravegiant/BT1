Audit Report

## Title
Withdrawal Payout Capped at Request-Time Price, Stranding Accrued Yield in Vault - (File: contracts/LRTWithdrawalManager.sol)

## Summary

`_calculatePayoutAmount` returns `min(expectedAssetAmount, currentReturn)`, capping the user's payout at the stale request-time price even when rsETH has appreciated. The full `rsETHUnstaked` is burned at current value, but the user receives only the lower, locked-in amount. The difference remains in `LRTUnstakingVault`, silently redistributed to remaining rsETH holders rather than credited to the withdrawing user.

## Finding Description

**Root cause:** `_calculatePayoutAmount` at L834:

```solidity
uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```

When rsETH appreciates between `initiateWithdrawal` and `unlockQueue`, `currentReturn > expectedAssetAmount`, so the function returns the stale `expectedAssetAmount`.

**Exploit flow:**

1. `initiateWithdrawal` (L166–175): User transfers rsETH; `expectedAssetAmount` is computed at current price and stored in `WithdrawalRequest`. `assetsCommitted[asset] += expectedAssetAmount`.

2. rsETH price rises during the 8-day delay (L94: `withdrawalDelayBlocks = 8 days / 12 seconds`).

3. `unlockQueue` → `_unlockWithdrawalRequests` (L798–807): `payoutAmount = _calculatePayoutAmount(...)` returns the stale `expectedAssetAmount`. The request's `expectedAssetAmount` is overwritten with `payoutAmount`, `rsETHAmountToBurn += request.rsETHUnstaked` (full rsETH), and `assetAmountToUnlock += payoutAmount` (only stale amount).

4. `unlockQueue` (L305): `IRSETH(...).burnFrom(address(this), rsETHBurned)` — full rsETH burned at current value.

5. `_processWithdrawalCompletion` (L734): `_transferAsset(asset, user, request.expectedAssetAmount)` — user receives only the stale amount.

**Why existing checks fail:** There are no guards that credit the user with appreciation occurring after `initiateWithdrawal`. The `assetsCommitted` accounting (L802: `assetsCommitted[asset] -= request.expectedAssetAmount`) correctly releases the original commitment, but the delta between `currentReturn` and `payoutAmount` is never attributed to anyone — it remains as excess backing in the vault.

## Impact Explanation

**High — Theft of unclaimed yield.**

The user's rsETH remains locked in the contract during the delay period, continuing to accrue value as EigenLayer staking rewards are reflected via `LRTOracle.updateRSETHPrice`. At unlock time, the protocol burns rsETH worth `currentReturn` in assets but pays out only `expectedAssetAmount`. The difference `currentReturn - expectedAssetAmount` is yield earned by the user's rsETH while it was locked, which is permanently lost to the withdrawing user and silently redistributed to remaining rsETH holders. This matches the allowed impact class "High. Theft of unclaimed yield."

## Likelihood Explanation

**High.** rsETH price increases continuously under normal protocol operation as staking rewards accrue. The default delay is 8 days (L94), during which price appreciation is near-certain. Every withdrawal processed during any period of rsETH appreciation triggers this loss. No attacker capability is required — the loss is automatic and affects every ordinary withdrawer.

## Recommendation

In `_calculatePayoutAmount`, remove the upside cap so users receive the full current fair value of their rsETH at unlock time. The cap should only apply on the downside (slashing/price drop) to protect the vault from over-commitment:

```solidity
function _calculatePayoutAmount(
    WithdrawalRequest storage request,
    uint256 rsETHPrice,
    uint256 assetPrice
) private view returns (uint256) {
    uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
-   return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
+   return currentReturn; // pay full current value; downside is naturally bounded by vault balance check
}
```

If the protocol intentionally caps upside (e.g., to prevent oracle-manipulation profit), the excess `currentReturn - expectedAssetAmount` must at minimum be explicitly credited to the user or routed to treasury, not left as silent vault surplus.

## Proof of Concept

1. rsETH price = 1.05 ETH/rsETH, stETH price = 1.00 ETH/stETH.
2. User calls `initiateWithdrawal(stETH, 1e18)`. `expectedAssetAmount = 1.05e18`. `assetsCommitted[stETH] += 1.05e18`.
3. 8 days pass. rsETH price rises to 1.08 ETH/rsETH (staking rewards accrued via `LRTOracle.updateRSETHPrice`).
4. Operator calls `unlockQueue(stETH, ...)`. `_calculatePayoutAmount` computes `currentReturn = 1.08e18`, returns `min(1.05e18, 1.08e18) = 1.05e18`. `rsETHAmountToBurn += 1e18`, `assetAmountToUnlock += 1.05e18`.
5. `burnFrom(address(this), 1e18)` — full 1 rsETH burned (worth 1.08 ETH at current price).
6. `unstakingVault.redeem(stETH, 1.05e18)` — only 1.05e18 stETH taken from vault.
7. User calls `completeWithdrawal`. Receives 1.05e18 stETH.
8. **Result**: User burned 1 rsETH worth 1.08 ETH but received only 1.05 ETH. 0.03 ETH worth of stETH remains in the vault, unattributed. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L94-94)
```text
        withdrawalDelayBlocks = 8 days / 12 seconds;
```

**File:** contracts/LRTWithdrawalManager.sol (L303-307)
```text
        );

        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTWithdrawalManager.sol (L734-734)
```text
        _transferAsset(asset, user, request.expectedAssetAmount);
```

**File:** contracts/LRTWithdrawalManager.sol (L800-807)
```text
            if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request

            assetsCommitted[asset] -= request.expectedAssetAmount;
            // Set the amount the user will receive
            request.expectedAssetAmount = payoutAmount;
            rsETHAmountToBurn += request.rsETHUnstaked;
            availableAssetAmount -= payoutAmount;
            assetAmountToUnlock += payoutAmount;
```

**File:** contracts/LRTWithdrawalManager.sol (L824-835)
```text
    function _calculatePayoutAmount(
        WithdrawalRequest storage request,
        uint256 rsETHPrice,
        uint256 assetPrice
    )
        private
        view
        returns (uint256)
    {
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
    }
```
