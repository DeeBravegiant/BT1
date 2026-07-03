Audit Report

## Title
Stale `rsETHPrice` in `instantWithdrawal` Allows Over-Extraction of Assets Before Loss Is Reflected — (File: `contracts/LRTWithdrawalManager.sol`)

## Summary

`LRTOracle.rsETHPrice` is a stored value updated only when `updateRSETHPrice()` is explicitly called. `instantWithdrawal` computes the payout using this stored price with no freshness check or min-cap, while the queued-withdrawal path (`_calculatePayoutAmount`) applies `min(expectedAssetAmount, currentReturn)` at unlock time. During the window between a slashing event and the next `updateRSETHPrice()` call, any rsETH holder can call `instantWithdrawal` at the pre-loss price, extracting more underlying assets than their proportional share and shifting the deficit onto all remaining holders.

## Finding Description

`rsETHPrice` is a plain storage variable in `LRTOracle`:

```solidity
uint256 public override rsETHPrice;   // LRTOracle.sol L28
```

It is only written inside `_updateRsETHPrice()`, which is invoked by the public `updateRSETHPrice()` or the manager-only `updateRSETHPriceAsManager()`. There is no automatic update on deposit, withdrawal, or slashing.

`getExpectedAssetAmount` reads this stored value directly:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
// LRTWithdrawalManager.sol L593
```

`instantWithdrawal` calls `getExpectedAssetAmount`, burns rsETH, and transfers the result to the caller with no correction:

```solidity
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
// ... vault balance check only (not a fair-share check) ...
_transferAsset(asset, msg.sender, userAmount);
// LRTWithdrawalManager.sol L228-250
```

The vault balance check (`assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)`) only prevents withdrawing more than the vault holds; it does not prevent paying out more than the caller's proportional share.

By contrast, `_calculatePayoutAmount` in the queued path caps the payout at the current fair value:

```solidity
uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
// LRTWithdrawalManager.sol L833-834
```

`instantWithdrawal` has no equivalent cap.

The downside-protection mechanism in `_updateRsETHPrice()` (lines 270–281 of `LRTOracle.sol`) pauses the protocol only when `updateRSETHPrice()` is called and the price drop exceeds `pricePercentageLimit`. It provides no protection during the window before that call, which is precisely when the stale price is exploitable.

**Exploit path:**
1. Protocol TVL = 300 ETH, rsETH supply = 300, `rsETHPrice = 1e18`, `isInstantWithdrawalEnabled[ETH] = true`, vault holds ≥ 100 ETH.
2. EigenLayer slashing removes 30 ETH from protocol TVL. `updateRSETHPrice()` has not been called; `rsETHPrice` remains `1e18`.
3. Attacker holds 100 rsETH and calls `instantWithdrawal(ETH_TOKEN, 100e18, "")`.
4. `getExpectedAssetAmount` returns `100e18 * 1e18 / 1e18 = 100 ETH` (stale price).
5. Vault balance check passes; 100 rsETH is burned; attacker receives 100 ETH.
6. `updateRSETHPrice()` is called: TVL = 170 ETH, supply = 200 rsETH → new price = 0.85 ETH/rsETH.
7. Remaining holders bear the attacker's 10 ETH excess extraction on top of the original 30 ETH loss.

## Impact Explanation

**Critical — Direct theft of user funds.**

The attacker burns the correct rsETH amount but receives more underlying assets than their proportional share. The deficit is permanently absorbed by all remaining rsETH holders, whose shares now back fewer real assets. This is a direct, quantifiable, and irreversible transfer of value from passive holders to the withdrawing attacker.

## Likelihood Explanation

**High.** EigenLayer slashing is an explicitly acknowledged protocol risk (the downside-protection pause mechanism exists for this reason). `updateRSETHPrice()` is never called atomically with a slashing event — there is always a non-zero window. `instantWithdrawal` is permissionless for any enabled asset. Any rsETH holder monitoring EigenLayer slashing events on-chain can detect the window and exploit it before the oracle is updated.

## Recommendation

Apply the same `min(expectedAssetAmount, currentReturn)` cap used in `_calculatePayoutAmount` inside `instantWithdrawal`. After computing `assetAmountUnlocked`, cap it to the current fair value derived from live oracle prices:

```solidity
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));
uint256 currentFairAmount = (rsETHUnstaked * lrtOracle.rsETHPrice()) / lrtOracle.getAssetPrice(asset);
if (currentFairAmount < assetAmountUnlocked) {
    assetAmountUnlocked = currentFairAmount;
}
```

Alternatively, call `updateRSETHPrice()` atomically at the start of `instantWithdrawal` to ensure the price is always fresh before computing the payout.

## Proof of Concept

**Foundry fork test outline:**

1. Fork mainnet at a block where `isInstantWithdrawalEnabled[ETH_TOKEN] = true` and `LRTUnstakingVault` holds ≥ 100 ETH.
2. Record `rsETHPrice` (e.g., `1e18`).
3. Simulate EigenLayer slashing by directly reducing the EigenLayer strategy's share value (or mock `getTotalAssetDeposits` to return a lower value), without calling `updateRSETHPrice()`.
4. As an unprivileged attacker holding 100 rsETH, call `instantWithdrawal(ETH_TOKEN, 100e18, "")`.
5. Assert attacker received 100 ETH (stale price payout).
6. Call `updateRSETHPrice()`.
7. Assert new `rsETHPrice < 0.9e18` (i.e., remaining holders bear more than the proportional loss).
8. Compare attacker's payout to `100 * newRsETHPrice / 1e18` to quantify the excess extraction.