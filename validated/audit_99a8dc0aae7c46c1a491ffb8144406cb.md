Audit Report

## Title
Stale `rsETHPrice` in `instantWithdrawal` Allows Exit at Pre-Slashing Price, Socializing Loss to Remaining Holders - (File: contracts/LRTWithdrawalManager.sol)

## Summary

`LRTWithdrawalManager.instantWithdrawal` computes asset payouts using the stored `rsETHPrice` from `LRTOracle` without forcing a price refresh. After an EigenLayer slashing event reduces the protocol's backing ETH, the stored price remains stale (inflated) until `updateRSETHPrice()` is explicitly called. Any rsETH holder can exploit this window by calling `instantWithdrawal` at the inflated price, extracting excess assets at the expense of remaining holders who bear the full loss.

## Finding Description

`LRTOracle.rsETHPrice` is a stored state variable updated only when `updateRSETHPrice()` (public, callable by anyone) or `updateRSETHPriceAsManager()` is called. It is not updated atomically with on-chain state changes in EigenLayer.

`instantWithdrawal` computes the payout via `getExpectedAssetAmount`:

```solidity
// LRTWithdrawalManager.sol:228
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
```

`getExpectedAssetAmount` reads the stored price directly with no freshness check:

```solidity
// LRTWithdrawalManager.sol:593
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

The payout is immediately transferred to the caller. There is no call to `updateRSETHPrice()` before computing the payout, and no minimum-output guard. [1](#0-0) [2](#0-1) [3](#0-2) 

**Contrast with the regular withdrawal path:** `_calculatePayoutAmount` applies `min(expectedAssetAmount, currentReturn)`, so if the price has dropped, users receive the lower current value. `instantWithdrawal` has no such protection. [4](#0-3) 

**Downside protection does not prevent the attack.** `_updateRsETHPrice()` contains a pause mechanism: if the price drop exceeds `pricePercentageLimit`, it pauses the deposit pool and withdrawal manager. [5](#0-4) 

However, this protection only triggers when `updateRSETHPrice()` is called. The attacker calls `instantWithdrawal` *before* `updateRSETHPrice()` is called — the pause has not yet occurred. Additionally, if `pricePercentageLimit` is set to `0`, the check `pricePercentageLimit > 0 && ...` is always false, disabling the pause entirely regardless of slashing magnitude. [6](#0-5) 

**Attack sequence:**
1. EigenLayer slashing reduces backing ETH. `rsETHPrice` in `LRTOracle` is still the old (higher) value.
2. Attacker calls `instantWithdrawal(asset, rsETHAmount, ...)` — burns rsETH and receives assets calculated at the stale high price.
3. `updateRSETHPrice()` is called (by bot or attacker) — price drops to reflect slashing.
4. Remaining holders now hold rsETH backed by fewer assets; the attacker extracted the difference.

Steps 2–4 can be bundled atomically in a single transaction.

## Impact Explanation

This is a **Critical** impact: direct theft of user funds. The attacker extracts real ETH/LST value from the protocol at the expense of remaining rsETH holders, whose shares are now backed by fewer assets. The magnitude scales with the slashing amount and the attacker's rsETH position. The loss is permanent and borne by passive holders who took no action.

## Likelihood Explanation

EigenLayer slashing is a known, anticipated risk for restaking protocols. `updateRSETHPrice()` is public and called by off-chain bots, not atomically with slashing events, so the attack window is non-zero and observable on-chain. Any rsETH holder can execute this without special permissions. The attack is repeatable after each slashing event. [7](#0-6) 

## Recommendation

1. **Force a price update before payout in `instantWithdrawal`:** Call `ILRTOracle(...).updateRSETHPrice()` at the start of `instantWithdrawal` to ensure the price is fresh before computing the payout. If the price drop triggers a pause, the withdrawal will revert, preventing exploitation.
2. **Add a minimum-output guard:** Allow callers to specify a `minAssetAmount` and revert if the computed payout falls below it.
3. **Amortize losses:** Implement a loss-amortization mechanism that spreads price decreases over multiple periods, preventing sudden exploitable price gaps.

## Proof of Concept

```
State: rsETHPrice = 1.05e18 (stale, pre-slashing)
True backing after slashing: 1.00e18 per rsETH

Attacker holds 100e18 rsETH.

Step 1: instantWithdrawal(ETH, 100e18)
  assetAmountUnlocked = 100e18 * 1.05e18 / 1e18 = 105 ETH  ← stale price used
  Attacker receives 105 ETH (minus fee), burns 100e18 rsETH

Step 2: updateRSETHPrice()
  rsETHPrice updated to 1.00e18

Step 3: depositETH{value: 105 ETH}()
  rsethAmountToMint = 105e18 * 1e18 / 1.00e18 = 105e18 rsETH

Net: Attacker started with 100e18 rsETH, ends with 105e18 rsETH.
Remaining holders: their rsETH is now backed by 5 ETH less than before.
```

**Foundry fork test plan:**
1. Fork mainnet with a deployed instance.
2. Manipulate EigenLayer state to simulate a slashing event (reduce backing ETH) without calling `updateRSETHPrice()`.
3. Call `instantWithdrawal` as an unprivileged rsETH holder.
4. Assert that the ETH received exceeds `rsETHBurned * newPrice / 1e18` (the fair post-slashing value).
5. Call `updateRSETHPrice()` and assert the price drops.
6. Verify the attacker's net position exceeds their starting rsETH equivalent value.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L228-228)
```text
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L248-250)
```text
        }

        _transferAsset(asset, msg.sender, userAmount);
```

**File:** contracts/LRTWithdrawalManager.sol (L590-593)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

**File:** contracts/LRTWithdrawalManager.sol (L833-834)
```text
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L270-281)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
```
