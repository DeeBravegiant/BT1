Audit Report

## Title
Stale rsETH Price in `instantWithdrawal()` Allows Users to Exit at Pre-Slashing Rate, Shifting Loss to Remaining Holders - (File: contracts/LRTWithdrawalManager.sol)

## Summary

`updateRSETHPrice()` in `LRTOracle.sol` is an unrestricted public function, meaning the on-chain `rsETHPrice` is only updated when someone explicitly calls it. When a slashing event reduces EigenLayer backing within the `pricePercentageLimit` threshold (not triggering a pause), any user can call `instantWithdrawal()` at the stale pre-slashing price and receive more assets than their proportional share. The excess is borne by remaining rsETH holders, constituting a direct theft of their unclaimed yield and principal backing.

## Finding Description

**Root cause:** `instantWithdrawal()` computes the payout by calling `getExpectedAssetAmount()`, which reads `lrtOracle.rsETHPrice()` at execution time with no snapshot, no minimum-of-two-prices guard, and no staleness check:

```solidity
// LRTWithdrawalManager.sol L228, L590-593
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
// ...
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

`updateRSETHPrice()` carries no access control and is callable by any address:

```solidity
// LRTOracle.sol L87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

**Why existing guards fail:**

The downside protection in `_updateRsETHPrice()` only pauses the protocol when the price drop **exceeds** `pricePercentageLimit`:

```solidity
// LRTOracle.sol L270-282
bool isPriceDecreaseOffLimit =
    pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);
if (isPriceDecreaseOffLimit) {
    if (!lrtDepositPool.paused()) lrtDepositPool.pause();
    if (!withdrawalManager.paused()) withdrawalManager.pause();
    _pause();
    return;
}
```

For slashing events whose magnitude falls **within** `pricePercentageLimit`, the price decreases without triggering a pause. `instantWithdrawal()` carries `whenNotPaused` and remains fully callable throughout this window.

The standard queued withdrawal path is protected by `_calculatePayoutAmount()`, which takes the **minimum** of the originally locked `expectedAssetAmount` and the current return:

```solidity
// LRTWithdrawalManager.sol L833-834
uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```

`instantWithdrawal()` has no equivalent protection. The attacker does not even need to front-run a specific transaction — they only need to observe the EigenLayer slashing event and call `instantWithdrawal()` before anyone calls `updateRSETHPrice()`. Because `updateRSETHPrice()` is public, the attacker can even call it themselves *after* their withdrawal to update the price, leaving no trace of manipulation.

**Exploit flow:**
1. Slashing event occurs on EigenLayer; `rsETHPrice` on-chain remains at pre-slashing value.
2. Attacker (any rsETH holder) calls `instantWithdrawal(asset, rsETHUnstaked, "")`.
3. `getExpectedAssetAmount()` returns `rsETHUnstaked * stalePrice / assetPrice` — inflated relative to true backing.
4. rsETH is burned at line 229; vault pays out at the inflated rate.
5. `updateRSETHPrice()` is called (by anyone); price drops to reflect slashing.
6. Remaining rsETH holders now hold claims against a vault that has paid out more than the post-slashing fair share, permanently diluting their positions.

## Impact Explanation

**High — Theft of unclaimed yield.**

The attacker extracts a quantifiable excess from the unstaking vault (3 ETH per 100 rsETH in the PoC example). This excess is not absorbed by the protocol; it is a direct, permanent reduction in the asset backing per rsETH for all remaining holders. Their future yield and principal claims are diluted in proportion to the attacker's over-extraction. This matches the allowed impact: **High — Theft of unclaimed yield**.

## Likelihood Explanation

**Medium.** Three conditions must hold:
1. `isInstantWithdrawalEnabled[asset]` is `true` — a manager toggle expected to be on in normal operation.
2. A slashing event occurs within `pricePercentageLimit` — the normal operating regime for minor slashing.
3. The attacker acts before `updateRSETHPrice()` is called — trivially achievable by monitoring EigenLayer events on-chain, with no mempool front-running required. The attacker can also call `updateRSETHPrice()` themselves after their withdrawal.

All three conditions represent the normal protocol state. The attack is repeatable across every qualifying slashing event.

## Recommendation

1. **Apply the same min-price guard used in `_calculatePayoutAmount()` to `instantWithdrawal()`.** Snapshot `rsETHPrice` at call time and cap the payout at `min(snapshotPrice, currentPrice)` — though for instant withdrawals the snapshot and current price are the same block, so the real fix is to call `updateRSETHPrice()` atomically at the start of `instantWithdrawal()` before computing the payout amount.

2. **Call `_updateRsETHPrice()` (or require a fresh price) at the start of `instantWithdrawal()`.** This eliminates the stale-price window entirely by forcing the price to reflect current EigenLayer state before computing the payout.

3. **Restrict `updateRSETHPrice()` to authorized callers.** The `updateRSETHPriceAsManager()` path already exists; making the public variant keeper-only removes the ability for anyone to delay the price update strategically.

## Proof of Concept

```
State before slashing:
  rsETHPrice = 1.05e18 (stored in LRTOracle)
  Actual backing per rsETH = 1.02e18 (after slashing on EigenLayer)
  pricePercentageLimit = 5e16 (5%) — slashing of ~2.86% is within limit, no pause triggered

Step 1: Slashing event occurs. rsETHPrice on-chain = 1.05e18 (stale).

Step 2: Attacker calls:
  LRTWithdrawalManager.instantWithdrawal(ETH, 100e18, "")

Step 3: getExpectedAssetAmount computes:
  assetAmountUnlocked = 100e18 * 1.05e18 / 1e18 = 105 ETH  (stale price)

Step 4: 100e18 rsETH burned (LRTWithdrawalManager.sol L229).
        Vault pays out 105 ETH to attacker.

Step 5: updateRSETHPrice() called (by anyone):
  rsETHPrice = 1.02e18

Step 6: Fair payout at correct price = 100e18 * 1.02e18 / 1e18 = 102 ETH.

Attacker received 105 ETH; fair share was 102 ETH.
Excess 3 ETH permanently borne by remaining rsETH holders.

Foundry fork test outline:
1. Fork mainnet; deploy/configure LRTOracle + LRTWithdrawalManager.
2. Set rsETHPrice = 1.05e18 via mock or direct storage write.
3. Reduce EigenLayer backing to simulate 1.02e18 true price.
4. Call instantWithdrawal() as attacker before updateRSETHPrice().
5. Assert attacker received 105 ETH.
6. Call updateRSETHPrice(); assert new price = 1.02e18.
7. Assert remaining holders' getExpectedAssetAmount() reflects diluted backing.
```