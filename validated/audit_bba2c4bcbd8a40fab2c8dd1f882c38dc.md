Audit Report

## Title
Stale `rsETHPrice` in `instantWithdrawal()` Enables Over-Extraction of Assets Before Oracle Update — (File: contracts/LRTWithdrawalManager.sol)

## Summary
`LRTOracle.updateRSETHPrice()` is a public function that updates the stored `rsETHPrice` only when explicitly called. When actual TVL decreases (e.g., EigenLayer slashing) before the oracle is refreshed, the stored price is stale and inflated. `instantWithdrawal()` computes the asset payout using this stale price with no freshness check and no `min()` guard, allowing any rsETH holder to extract more assets than their proportional entitlement. The shortfall is socialized among remaining rsETH holders when the oracle is eventually updated.

## Finding Description
`LRTOracle.updateRSETHPrice()` is callable by anyone with no access control beyond `whenNotPaused`:

```solidity
// LRTOracle.sol L87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

The stored `rsETHPrice` is only updated when this function is called. `_updateRsETHPrice()` computes the new price from live on-chain TVL:

```solidity
// LRTOracle.sol L250
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

When TVL decreases (e.g., EigenLayer slashing reduces `getEffectivePodShares()`), the stored `rsETHPrice` remains at the pre-loss value until `updateRSETHPrice()` is called.

`instantWithdrawal()` computes the payout directly from the stored price:

```solidity
// LRTWithdrawalManager.sol L228
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
```

```solidity
// LRTWithdrawalManager.sol L593
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

There is no call to `updateRSETHPrice()` before this computation, and no `min()` guard. By contrast, the regular queued-withdrawal path applies:

```solidity
// LRTWithdrawalManager.sol L833-834
uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```

This `min()` in `_calculatePayoutAmount()` caps the payout at the lower of the locked-in expected amount and the current return, protecting against price inflation. `instantWithdrawal()` has no equivalent protection.

The only guard in `instantWithdrawal()` is:

```solidity
// LRTWithdrawalManager.sol L231-232
if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
    revert CantInstantWithdrawMoreThanAvailable();
}
```

This limits the withdrawal to what is physically present in the unstaking vault but does not prevent the stale-price over-computation — the attacker simply receives more assets per rsETH burned than they are entitled to, up to the vault's available balance.

**Exploit flow:**
1. EigenLayer slashing reduces on-chain TVL. Stored `rsETHPrice` = `P_high` (stale).
2. Attacker observes the state change. `updateRSETHPrice()` has not been called.
3. Attacker calls `instantWithdrawal(asset, rsETHAmount, "")`.
   - `assetAmountUnlocked = rsETHAmount * P_high / assetPrice` (inflated)
   - rsETH is burned; attacker receives `assetAmountUnlocked` from `LRTUnstakingVault`.
4. `updateRSETHPrice()` is called. New price `P_low = (TVL_slashed) / rsethSupply` is stored.
5. Remaining rsETH holders now hold rsETH backed by fewer assets per token. The attacker's excess `rsETHAmount * (P_high - P_low) / assetPrice` is a direct loss to them.

## Impact Explanation
**High — Theft of unclaimed yield.** The excess assets extracted by the attacker represent the accrued yield/appreciation that remaining rsETH holders believed they held. After the oracle update, the price drops to reflect the real TVL, and the shortfall — equal to `rsETHAmount * (P_high - P_low) / assetPrice` — is permanently borne by all remaining rsETH holders. This is a concrete, quantifiable extraction of value from other protocol participants, matching the "Theft of unclaimed yield" impact class.

## Likelihood Explanation
**Medium.** The attack window opens whenever actual TVL decreases before `updateRSETHPrice()` is called. EigenLayer slashing events are observable on-chain without any privileged access. The attacker does not need to front-run any specific transaction — they only need to act within the window between the TVL decrease and the next oracle update. The only prerequisite is that `instantWithdrawal` is enabled for the target asset (a manager-controlled flag), which is a normal operational state. The attack is repeatable across any such event.

## Recommendation
1. **Call `updateRSETHPrice()` atomically inside `instantWithdrawal()`** before computing `assetAmountUnlocked`, ensuring the price is always fresh at execution time.
2. Alternatively, apply the same `min(expectedAmount, currentReturn)` guard used in `_calculatePayoutAmount()` to `instantWithdrawal()`, computing `currentReturn` from live TVL rather than the stored price.
3. As a defense-in-depth measure, require that `updateRSETHPrice()` has been called within a recent block window before any withdrawal is processed.

## Proof of Concept
**Foundry fork test outline:**

```solidity
// 1. Fork mainnet at a block where rsETHPrice = P_high
// 2. Simulate EigenLayer slashing: mock getEffectivePodShares() to return a reduced value
//    such that _getTotalEthInProtocol() returns TVL - X
// 3. Confirm updateRSETHPrice() has NOT been called (rsETHPrice still = P_high)
// 4. Attacker (rsETH holder) calls instantWithdrawal(asset, rsETHAmount, "")
//    - Record assetReceived = actual assets transferred to attacker
// 5. Call updateRSETHPrice() → rsETHPrice drops to P_low
// 6. Compute fair_amount = rsETHAmount * P_low / assetPrice
// 7. Assert assetReceived > fair_amount
//    → excess = assetReceived - fair_amount is the stolen yield
// 8. Assert remaining rsETH holders' backing per token has decreased by excess / remainingSupply
```

The test concretely demonstrates that `assetReceived` exceeds `fair_amount` by `rsETHAmount * (P_high - P_low) / assetPrice`, confirming the theft of unclaimed yield from remaining holders.