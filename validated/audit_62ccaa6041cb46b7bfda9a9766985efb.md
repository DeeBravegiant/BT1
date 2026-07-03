Audit Report

## Title
`instantWithdrawal()` Uses Stale `rsETHPrice` With No Minimum-Price Guard, Enabling Pre-Slashing Exit at Inflated Rate - (File: contracts/LRTWithdrawalManager.sol)

## Summary

`LRTOracle.updateRSETHPrice()` is an unrestricted public function, meaning its pending mempool transaction is publicly observable. When a slashing event reduces EigenLayer backing within the `pricePercentageLimit` band (no auto-pause), any user can call `instantWithdrawal()` before the price update is mined and receive assets computed at the stale pre-slashing `rsETHPrice`. The slashing loss that should be distributed proportionally across all rsETH holders is instead concentrated on those who did not exit, constituting a theft of yield/value from remaining holders.

## Finding Description

`updateRSETHPrice()` carries no access control and is callable by any address:

```solidity
// LRTOracle.sol L87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

`instantWithdrawal()` computes the payout by reading `lrtOracle.rsETHPrice()` at execution time via `getExpectedAssetAmount()`:

```solidity
// LRTWithdrawalManager.sol L228
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);

// LRTWithdrawalManager.sol L590-593
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

There is no snapshot, no lock-in, and no minimum-of-two-prices guard in the instant withdrawal path. The downside protection in `_updateRsETHPrice()` only pauses the protocol when the price drop **exceeds** `pricePercentageLimit`:

```solidity
// LRTOracle.sol L270-282
bool isPriceDecreaseOffLimit =
    pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);
if (isPriceDecreaseOffLimit) {
    ...pause...
    return;
}
```

For slashing events whose magnitude falls **within** `pricePercentageLimit`, the price decreases without triggering a pause. `instantWithdrawal()` carries `whenNotPaused`, so it remains fully callable throughout this window.

By contrast, the standard `initiateWithdrawal()` / `unlockQueue()` path is protected: `_calculatePayoutAmount()` takes the **minimum** of the originally locked `expectedAssetAmount` and the current return, so a price drop after request time reduces the payout:

```solidity
// LRTWithdrawalManager.sol L833-834
uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```

`instantWithdrawal()` has no equivalent protection. The attacker's rsETH is burned (L229), so the supply decreases while the vault pays out at the inflated rate, permanently diluting remaining holders' claims.

## Impact Explanation

**High — Theft of unclaimed yield / avoidance of loss at the expense of other rsETH holders.**

A user who exits via `instantWithdrawal()` before `updateRSETHPrice()` is mined receives assets valued at the pre-slashing rate. The slashing loss that should have been distributed proportionally across all rsETH holders is instead concentrated on those who did not exit. The attacker's gain is a direct, quantifiable transfer of value from remaining holders. The excess payout is bounded only by the attacker's rsETH balance and the vault's available liquidity for instant withdrawal.

## Likelihood Explanation

**Medium.** Three conditions must hold simultaneously:

1. `isInstantWithdrawalEnabled[asset]` is `true` — a manager-controlled toggle expected to be enabled in normal operation.
2. A slashing event occurs whose magnitude is within `pricePercentageLimit` (i.e., does not trigger an automatic pause) — this is the normal operating regime for minor slashing.
3. The attacker acts before the price update is mined — straightforward, since `updateRSETHPrice()` is public and its pending transaction is visible in the mempool. A monitoring bot can reliably detect the slashing event on EigenLayer and submit `instantWithdrawal()` with a higher gas price.

Conditions 1 and 2 represent the normal protocol state. Condition 3 is a standard mempool front-run, well within the capability of any sophisticated actor.

## Recommendation

1. **Apply the same `min(lockedPrice, currentPrice)` guard used in `_calculatePayoutAmount()` to `instantWithdrawal()`.** Snapshot `rsETHPrice` at call time and use `min(snapshotPrice, currentPrice)` to compute the payout, ensuring a price drop between observation and execution cannot be exploited.

2. **Introduce a short mandatory delay for instant withdrawals.** Even a 1–2 block delay eliminates the mempool front-run vector, since `updateRSETHPrice()` would be mined before the withdrawal completes.

3. **Restrict `updateRSETHPrice()` to authorized callers.** Making it callable only by a keeper role or the manager removes the public mempool signal that enables the front-run. The `updateRSETHPriceAsManager()` path already exists for privileged updates.

## Proof of Concept

```
State before slashing:
  rsETHPrice = 1.05e18 (stored in LRTOracle)
  Actual backing per rsETH = 1.02e18 (after slashing event on EigenLayer)
  pricePercentageLimit = 5% (slashing of ~2.86% is within limit, no auto-pause)

1. Slashing event occurs on EigenLayer. rsETHPrice is still 1.05e18 on-chain.

2. Keeper submits updateRSETHPrice() → mempool (would set rsETHPrice = 1.02e18).

3. Attacker observes the pending tx. Submits instantWithdrawal(ETH, 100e18, "")
   with higher gas, front-running the keeper.

4. instantWithdrawal() executes:
     assetAmountUnlocked = getExpectedAssetAmount(ETH, 100e18)
                         = 100e18 * 1.05e18 / 1e18   // stale price
                         = 105 ETH

5. updateRSETHPrice() mines next:
     rsETHPrice = 1.02e18

6. Fair payout at correct price = 100e18 * 1.02e18 / 1e18 = 102 ETH.

Attacker extracted 105 ETH instead of 102 ETH.
Excess 3 ETH is borne by remaining rsETH holders.
```

**Foundry fork test plan:**
- Fork mainnet at a block where `isInstantWithdrawalEnabled[ETH]` is `true`.
- Manipulate `rsETHPrice` storage slot to simulate a pre-slashing value (e.g., 1.05e18) while `_getTotalEthInProtocol()` would return a lower value (1.02e18 backing).
- Call `instantWithdrawal(ETH, 100e18, "")` as the attacker before calling `updateRSETHPrice()`.
- Assert attacker received 105 ETH.
- Call `updateRSETHPrice()` and assert `rsETHPrice` is now 1.02e18.
- Assert remaining rsETH holders' per-token backing is lower than it would have been had the attacker exited at the correct price.