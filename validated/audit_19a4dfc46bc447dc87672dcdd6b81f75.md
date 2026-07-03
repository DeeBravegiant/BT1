All cited code references are confirmed in the repository. Let me verify the full exploit path logic before rendering a verdict.

**Zero-supply branch:** [1](#0-0)  unconditionally resets `rsETHPrice = 1e18` without calling `_getTotalEthInProtocol()`.

**Residual accumulation mechanism:** `_calculatePayoutAmount` returns `min(expectedAssetAmount, currentReturn)`. [2](#0-1)  When sfrxETH's `pricePerShare` rises between initiation and unlock, `assetPrice_unlock > assetPrice_init`, so `currentReturn = rsETHUnstaked * rsETHPrice / assetPrice_unlock < expectedAssetAmount`. The payout is `currentReturn`; only `currentReturn` is redeemed from the vault, leaving `expectedAssetAmount - currentReturn` as residual sfrxETH.

**Residual counted in TVL:** `getTotalAssetDeposits` includes `assetLyingUnstakingVault = IERC20(asset).balanceOf(lrtUnstakingVault)`. [3](#0-2)  This residual is therefore included in `_getTotalEthInProtocol()` — but only when `rsethSupply > 0`. When supply is zero, the branch returns before calling it.

**Mint pricing:** `getRsETHAmountToMint` divides by the stored `rsETHPrice`. [4](#0-3)  With `rsETHPrice` reset to `1e18` and `sfrxETH.pricePerShare() = 1.1e18`, a deposit of `D` sfrxETH mints `1.1D` rsETH — but the protocol already holds residual `R` sfrxETH, so the minted rsETH represents `D + R` sfrxETH worth of backing.

The exploit path is arithmetically sound and all code references are accurate.

---

Audit Report

## Title
rsETH Price Reset to 1e18 on Zero Supply Ignores Residual sfrxETH Holdings, Enabling First-Minter Yield Theft — (`contracts/LRTOracle.sol`)

## Summary
`LRTOracle._updateRsETHPrice` unconditionally resets `rsETHPrice` to `1e18` when `rsethSupply == 0` without inspecting actual protocol holdings. If sfrxETH yield has accrued between withdrawal initiation and unlock, a residual sfrxETH balance remains in `LRTUnstakingVault` after all rsETH is burned. The first subsequent depositor receives rsETH priced at `1e18` while the protocol's true backing per rsETH is materially higher, allowing that depositor to drain the residual sfrxETH they did not contribute.

## Finding Description

**Root cause — `LRTOracle._updateRsETHPrice` (lines 218–222):**

```solidity
if (rsethSupply == 0) {
    rsETHPrice = 1 ether;
    highestRsethPrice = 1 ether;
    return;
}
```

The branch returns before calling `_getTotalEthInProtocol()`, so any sfrxETH still tracked by `getTotalAssetDeposits` is silently ignored.

**How residual sfrxETH accumulates:**

`_calculatePayoutAmount` returns `min(expectedAssetAmount, currentReturn)` where `currentReturn = rsETHUnstaked * rsETHPrice / assetPrice`. Because `SfrxETHPriceOracle.getAssetPrice` always returns the live `pricePerShare`, if sfrxETH appreciates between withdrawal initiation and `unlockQueue` execution, `assetPrice_unlock > assetPrice_init`, making `currentReturn < expectedAssetAmount`. The vault redeems only `currentReturn`; the difference `expectedAssetAmount - currentReturn` remains as residual sfrxETH in `LRTUnstakingVault`. After all rsETH is burned, this residual persists and is still counted by `getTotalAssetDeposits` via `assetLyingUnstakingVault = IERC20(asset).balanceOf(lrtUnstakingVault)`.

**Exploit flow (with `sfrxETH.pricePerShare() = 1.1e18`, residual `R` sfrxETH in vault, `rsethSupply = 0`):**

1. Call `updateRSETHPrice()` → zero-supply branch fires → `rsETHPrice = 1e18`.
2. Attacker calls `depositAsset(sfrxETH, D, 0, "")` → `rsethAmountToMint = D * 1.1e18 / 1e18 = 1.1D` rsETH minted. Protocol now holds `D` sfrxETH in deposit pool + `R` sfrxETH in vault.
3. Call `updateRSETHPrice()` → `totalETHInProtocol = (D + R) * 1.1e18`, `rsETHPrice = (D + R) * 1.1e18 / 1.1D = (D+R)/D * 1e18`.
4. Attacker calls `initiateWithdrawal(sfrxETH, 1.1D, "")` → `expectedAssetAmount = 1.1D * (D+R)/D / 1.1 = D + R`. `getAvailableAssetAmount = D + R` ✓.
5. Operator transfers `D` sfrxETH from deposit pool to vault (routine operation).
6. Operator calls `unlockQueue` → `payoutAmount = D + R`, burns `1.1D` rsETH, redeems `D + R` from vault.
7. Attacker calls `completeWithdrawal` → receives `D + R` sfrxETH.

**Result:** Attacker deposited `D` sfrxETH and withdrew `D + R` sfrxETH, extracting `R` sfrxETH of residual yield they did not deposit. No existing guard in `depositAsset`, `_beforeDeposit`, or `updateRSETHPrice` checks for non-zero holdings when supply is zero.

## Impact Explanation

The attacker extracts sfrxETH yield that accumulated in `LRTUnstakingVault` during the withdrawal lifecycle and was not distributed to withdrawing users (who received the minimum payout). This is a direct, quantifiable theft of unclaimed yield from the protocol. The stolen amount equals the residual sfrxETH left in the vault after the final `unlockQueue` call.

**Impact: High — Theft of unclaimed yield.**

## Likelihood Explanation

The precondition requires `rsethSupply` reaching exactly zero while sfrxETH remains in the vault. This requires a complete protocol wind-down (e.g., migration or emergency shutdown) combined with sfrxETH yield having accrued between withdrawal initiation and unlock. The minimum-payout logic in `_calculatePayoutAmount` structurally produces a residual whenever `pricePerShare` rises between initiation and unlock, which is the normal behavior of sfrxETH. A complete wind-down is a realistic lifecycle event. The attacker needs no special privileges — only the ability to call `depositAsset` and `initiateWithdrawal`.

**Likelihood: Low.**

## Recommendation

Replace the unconditional early return with a branch that checks actual holdings:

```solidity
if (rsethSupply == 0) {
    uint256 totalETH = _getTotalEthInProtocol();
    if (totalETH == 0) {
        rsETHPrice = 1 ether;
        highestRsethPrice = 1 ether;
    }
    // If totalETH > 0 with zero supply, leave rsETHPrice unchanged
    // to block new mints until residual is swept or redistributed.
    return;
}
```

Additionally, add a guard in `_beforeDeposit` / `depositETH` that reverts when `rsethSupply == 0` but `_getTotalEthInProtocol() > 0`, preventing any mint until governance explicitly resolves the residual (e.g., via `sweepRemainingAssets` or redistribution).

## Proof of Concept

```
Setup (fork or unit test):
  sfrxETH.pricePerShare = 1.1e18
  LRTUnstakingVault holds R = 10e18 sfrxETH (residual after wind-down)
  rsETH.totalSupply() == 0

Step 1: updateRSETHPrice()
  → rsETHPrice = 1e18 (zero-supply branch)

Step 2: attacker.depositAsset(sfrxETH, 1e18, 0, "")
  → rsethAmountToMint = 1e18 * 1.1e18 / 1e18 = 1.1e18 rsETH
  → deposit pool holds 1e18 sfrxETH, vault holds 10e18 sfrxETH

Step 3: updateRSETHPrice()
  → totalETHInProtocol = 11e18 * 1.1e18 / 1e18 = 12.1e18
  → rsETHPrice = 12.1e18 / 1.1e18 = 11e18

Step 4: attacker.initiateWithdrawal(sfrxETH, 1.1e18, "")
  → expectedAssetAmount = 1.1e18 * 11e18 / 1.1e18 = 11e18 sfrxETH

Step 5: operator.transferAssetToLRTUnstakingVault(sfrxETH, 1e18)
Step 6: operator.unlockQueue(sfrxETH, ...) → burns 1.1e18 rsETH, redeems 11e18 sfrxETH
Step 7: attacker.completeWithdrawal(sfrxETH, "")
  → attacker receives 11e18 sfrxETH

Assert: attacker deposited 1e18 sfrxETH (1.1e18 ETH value), withdrew 11e18 sfrxETH (12.1e18 ETH value).
Invariant broken: 10e18 sfrxETH of residual yield extracted by first minter.
```

### Citations

**File:** contracts/LRTOracle.sol (L218-222)
```text
        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }
```

**File:** contracts/LRTWithdrawalManager.sol (L833-834)
```text
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```

**File:** contracts/LRTDepositPool.sol (L458-461)
```text
        address lrtUnstakingVault = lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT);

        assetLyingInConverter = 0; // assets in converter are accounted in their eth value => getETHDistributionData()
        assetLyingUnstakingVault = IERC20(asset).balanceOf(lrtUnstakingVault);
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
