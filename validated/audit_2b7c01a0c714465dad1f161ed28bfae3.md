Audit Report

## Title
Permissionless `FeeReceiver.sendFunds()` + `LRTOracle.updateRSETHPrice()` Enables Front-Running Yield Theft from rsETH Holders - (`contracts/FeeReceiver.sol`, `contracts/LRTOracle.sol`)

## Summary
`FeeReceiver.sendFunds()` has no access control and can be called by any EOA, allowing anyone to flush accumulated MEV/execution-layer rewards into the deposit pool at will. Combined with the equally permissionless `LRTOracle.updateRSETHPrice()`, an attacker can atomically deposit ETH, trigger reward distribution, update the rsETH price upward, and immediately withdraw at the inflated price via `instantWithdrawal()` — capturing yield that should have accrued exclusively to pre-existing rsETH holders.

## Finding Description

**Root cause 1 — `FeeReceiver.sendFunds()` has no access control:**

```solidity
// contracts/FeeReceiver.sol L53-58
function sendFunds() external {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```

Any caller can flush the entire ETH balance of `FeeReceiver` into `LRTDepositPool` at any time. This increases `address(this).balance` of the deposit pool, which is included in `getETHDistributionData()` → `getTotalAssetDeposits(ETH)` → `_getTotalEthInProtocol()`.

**Root cause 2 — `LRTOracle.updateRSETHPrice()` is permissionless:**

```solidity
// contracts/LRTOracle.sol L87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

`_updateRsETHPrice()` computes `newRsETHPrice = (totalETHInProtocol - protocolFeeInETH) / rsethSupply`. After `sendFunds()` inflates the numerator, calling `updateRSETHPrice()` writes the higher price to `rsETHPrice`.

**Partial mitigation that does NOT block the attack — `pricePercentageLimit`:**

`_updateRsETHPrice()` checks whether the new price exceeds `highestRsethPrice` by more than `pricePercentageLimit`. If it does and the caller is not a MANAGER, it reverts with `PriceAboveDailyThreshold`. However:
- `pricePercentageLimit` defaults to `0` (unset at initialization), meaning the check is skipped entirely (`pricePercentageLimit > 0` is false).
- Even when set, the attacker can size their deposit `A` so that the resulting price increase `R/(T+A)` stays below the configured threshold. With a large enough `A` relative to `R`, the price increase is arbitrarily small and always passes.

**Exploit path (instant withdrawal variant):**

1. Attacker calls `LRTDepositPool.depositETH{value: A}(0, "")` → receives `A * rsETHPrice_old / 1e18` rsETH at the current (pre-reward) price.
2. Attacker calls `FeeReceiver.sendFunds()` → `R` ETH transferred to deposit pool; TVL increases by `R`.
3. Attacker calls `LRTOracle.updateRSETHPrice()` → new price = `(T + A + R) / (S + A*S/T)`, higher than before.
4. Attacker calls `LRTWithdrawalManager.instantWithdrawal(ETH, rsETHAmount, "")` → `getExpectedAssetAmount` uses the now-inflated `lrtOracle.rsETHPrice()`, returning more ETH than deposited.

`instantWithdrawal` uses the live oracle price at execution time with no snapshot protection:
```solidity
// contracts/LRTWithdrawalManager.sol L228, L593
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
// ...
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

The attacker's profit is `A·R/(T+A)` ETH, which is a direct transfer of yield from existing holders to the attacker. The `instantWithdrawal` path is gated by `isInstantWithdrawalEnabled[asset]` and `getAssetsAvailableForInstantWithdrawal`, but when ETH instant withdrawal is enabled and the unstaking vault holds sufficient ETH, the entire attack is atomic within a single block.

For the queued withdrawal path, the `expectedAssetAmount` is locked at `initiateWithdrawal` time using the current oracle price, so the attacker must call `updateRSETHPrice()` *before* `initiateWithdrawal` — still achievable in the same block as `sendFunds()`.

## Impact Explanation

**High — Theft of unclaimed yield.**

Existing rsETH holders are entitled to the rewards accumulated in `FeeReceiver`. By front-running the reward flush with a deposit and immediately withdrawing at the inflated price, the attacker extracts `A·R/(T+A)` ETH of yield that belongs to pre-existing holders. This is a direct, quantifiable, repeatable theft of unclaimed yield matching the allowed impact scope exactly.

## Likelihood Explanation

- Both `sendFunds()` and `updateRSETHPrice()` require zero privileges and no preconditions beyond the contracts not being paused.
- MEV bots routinely monitor mempool and on-chain state for exactly this pattern.
- The attack is profitable whenever `A·R/(T+A)` exceeds the instant-withdrawal fee cost; with large `A` and non-trivial `R`, this is easily satisfied.
- `pricePercentageLimit` is `0` by default and, even when set, can be circumvented by sizing `A` appropriately.
- No privileged access, leaked keys, or external oracle compromise is required.
- Repeatable every time rewards accumulate in `FeeReceiver`.

## Recommendation

1. **Restrict `FeeReceiver.sendFunds()`** to a trusted role (e.g., `MANAGER`):
   ```solidity
   function sendFunds() external onlyRole(LRTConstants.MANAGER) { ... }
   ```

2. **Restrict `LRTOracle.updateRSETHPrice()`** to a keeper/manager role, or enforce a minimum time between successive price updates (e.g., 1 hour), preventing atomic deposit → price-update sequences.

3. **Snapshot the rsETH price at `initiateWithdrawal` time** and use `min(snapshotPrice, currentPrice)` at unlock time (already partially done for queued withdrawals via `_calculatePayoutAmount`, but the snapshot itself is taken at the inflated price if the attacker acts first).

4. Consider a **minimum accumulation threshold** in `FeeReceiver` before `sendFunds()` can be called, reducing the frequency and profitability of the attack.

## Proof of Concept

```
Setup:
  Protocol TVL (ETH) = 10,000 ETH
  rsETH supply = 9,500 (price ≈ 1.0526 ETH/rsETH)
  FeeReceiver balance = 50 ETH (accumulated MEV rewards)
  isInstantWithdrawalEnabled[ETH] = true
  pricePercentageLimit = 0 (default)

Attack (single block / single tx via contract):

1. attacker.depositETH{value: 5000 ETH}(0, "")
   → mints 5000 / 1.0526 ≈ 4750 rsETH

2. attacker → FeeReceiver.sendFunds()
   → 50 ETH sent to DepositPool
   → TVL = 15,050 ETH, rsETH supply = 14,250

3. attacker → LRTOracle.updateRSETHPrice()
   → newRsETHPrice = 15,050 / 14,250 ≈ 1.05614 ETH/rsETH
   (price increase passes threshold check since pricePercentageLimit == 0)

4. attacker → LRTWithdrawalManager.instantWithdrawal(ETH, 4750, "")
   → assetAmountUnlocked = 4750 × 1.05614 ≈ 5016.7 ETH
   → attacker receives ≈ 5016.7 ETH (minus instant fee)
   → net profit ≈ 16.7 ETH stolen from existing holders

Existing holders' loss: their proportional share of the 50 ETH reward
is diluted by the attacker's 5000 ETH deposit, with the attacker
capturing ≈ 5000/15000 × 50 ≈ 16.7 ETH of the reward.
```