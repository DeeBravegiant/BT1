Audit Report

## Title
`instantWithdrawal()` Pays Out at Stale `rsETHPrice` After EigenLayer Slashing, Enabling Value Extraction from Remaining rsETH Holders — (File: contracts/LRTWithdrawalManager.sol)

## Summary
`instantWithdrawal()` computes the asset payout using the stored `rsETHPrice` state variable in `LRTOracle`, which is only updated when `updateRSETHPrice()` is explicitly called. After an EigenLayer slashing event reduces the protocol's underlying asset balances, a window exists before `updateRSETHPrice()` is called during which any rsETH holder can call `instantWithdrawal()` and receive assets at the pre-slash rate. The slashing loss is then fully absorbed by remaining rsETH holders when the oracle is eventually updated.

## Finding Description

`instantWithdrawal()` computes the payout at line 228 by calling `getExpectedAssetAmount()`:

```solidity
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
```

`getExpectedAssetAmount()` reads the stored `rsETHPrice` state variable directly from `LRTOracle`:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

`rsETHPrice` is a state variable in `LRTOracle` that is only written in `_updateRsETHPrice()`, which is only reachable via the explicit public call `updateRSETHPrice()` (or the manager-gated `updateRSETHPriceAsManager()`). There is no call to `updateRSETHPrice()` inside `instantWithdrawal()`.

When EigenLayer slashing occurs, the NodeDelegator's share balance decreases. `getTotalAssetDeposits()` in `LRTDepositPool` aggregates `getAssetBalance()` across all NodeDelegators, which reads live EigenLayer share values. Therefore, `_getTotalEthInProtocol()` in `LRTOracle` would immediately reflect the lower value — but only when `updateRSETHPrice()` is called. Until that call, `rsETHPrice` remains at the pre-slash value.

The downside-protection auto-pause in `LRTOracle._updateRsETHPrice()` (lines 270–281) only fires when `updateRSETHPrice()` is actually invoked. Before that call, the protocol is not paused and `instantWithdrawal()` remains open at the stale price.

By contrast, the queued withdrawal path (`_calculatePayoutAmount()`) takes the **minimum** of the amount locked at request time and the amount recomputed at unlock time:

```solidity
uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```

`instantWithdrawal()` has no equivalent protection. The `CantInstantWithdrawMoreThanAvailable` check only guards against draining the vault beyond its liquid balance — it does not guard against the price being stale.

## Impact Explanation

An rsETH holder who observes an EigenLayer slashing event on-chain before `updateRSETHPrice()` is called can call `instantWithdrawal()` and receive assets valued at the pre-slash rsETH price. The slashing loss is then borne entirely by remaining rsETH holders when the oracle is eventually updated and the price drops. This is a direct transfer of value from remaining rsETH holders to the withdrawing attacker.

**Impact: High — Theft of unclaimed yield / value from remaining rsETH holders.** The attacker extracts more assets than their rsETH is worth post-slash, at the expense of other protocol participants.

## Likelihood Explanation

- EigenLayer slashing is an explicitly anticipated event; `LRTUnstakingVault` imports and uses `SlashingLib`.
- `updateRSETHPrice()` is a public function that must be called explicitly; it is never called atomically with slashing. The staleness window is bounded only by off-chain operator response time.
- `rsETHPrice` is a public state variable; any on-chain observer can detect staleness by comparing it to a fresh `_getTotalEthInProtocol()` computation.
- `isInstantWithdrawalEnabled[asset]` must be `true`, but this is an operational configuration that is expected to be enabled for supported assets.
- No special privileges are required beyond holding rsETH.

## Recommendation

1. Inside `instantWithdrawal()`, call `updateRSETHPrice()` (or an equivalent fresh price computation) before computing `assetAmountUnlocked`, so the payout always reflects the current state of the protocol.
2. Alternatively, apply the same `_calculatePayoutAmount()` minimum-of-locked-vs-current logic to `instantWithdrawal()` by snapshotting the price at call time and comparing it to the stored price.
3. At minimum, add a single-block delay to `instantWithdrawal()` to prevent same-block exploitation of oracle updates.

## Proof of Concept

1. EigenLayer slashing is finalized on-chain; the NodeDelegator's share balance decreases, reducing the true value of rsETH.
2. `rsETHPrice` in `LRTOracle` is still the pre-slash value because `updateRSETHPrice()` has not yet been called.
3. Attacker (rsETH holder) calls `instantWithdrawal(asset, rsETHAmount, "")`.
   - `getExpectedAssetAmount()` computes `rsETHAmount * rsETHPrice_stale / assetPrice`, returning a larger asset amount than the post-slash fair value. [1](#0-0) 
   - The `CantInstantWithdrawMoreThanAvailable` check passes as long as the vault has sufficient liquid balance. [2](#0-1) 
4. Attacker's rsETH is burned and they receive the inflated asset amount from `LRTUnstakingVault` via `unstakingVault.redeem()`. [3](#0-2) 
5. `updateRSETHPrice()` is eventually called; `_updateRsETHPrice()` computes a lower `newRsETHPrice` from the reduced `_getTotalEthInProtocol()` and writes it to `rsETHPrice`. [4](#0-3) 
6. Remaining rsETH holders now hold rsETH worth less than before the slash, having absorbed the full slashing loss that the attacker escaped.

**Foundry fork test outline:**
- Fork mainnet at a block after a slashing event (or simulate by directly reducing a NodeDelegator's EigenLayer shares in a local fork).
- Verify `lrtOracle.rsETHPrice()` is stale (pre-slash).
- Call `instantWithdrawal()` as an rsETH holder; record assets received.
- Call `updateRSETHPrice()`; record new `rsETHPrice`.
- Assert that assets received exceed `rsETHAmount * newRsETHPrice / assetPrice`, confirming the attacker extracted value at the stale rate.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L228-228)
```text
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L231-233)
```text
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }
```

**File:** contracts/LRTWithdrawalManager.sol (L235-235)
```text
        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```
