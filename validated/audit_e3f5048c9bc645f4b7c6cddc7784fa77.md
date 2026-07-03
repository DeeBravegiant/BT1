Looking at the actual code to verify each claim in the submission.

Audit Report

## Title
Stale `rsETHPrice` Enables Deposit-UpdatePrice-InstantWithdraw Profit Extraction — (`contracts/LRTOracle.sol`, `contracts/LRTDepositPool.sol`, `contracts/LRTWithdrawalManager.sol`)

## Summary
`LRTOracle` stores `rsETHPrice` as a state variable that is only refreshed on explicit calls to `updateRSETHPrice()`, which is a public, permissionless function. An attacker can deposit at the stale (lower) price to receive an inflated rsETH amount, immediately trigger a price update, and then call `instantWithdrawal` at the freshly updated (higher) price — extracting the accrued yield that belongs to existing rsETH holders in a single atomic sequence. The `pricePercentageLimit` guard bounds the per-cycle profit but does not prevent the attack.

## Finding Description
`LRTOracle` holds `rsETHPrice` as a persistent state variable updated only inside `_updateRsETHPrice()`:

```solidity
// LRTOracle.sol L28
uint256 public override rsETHPrice;
// LRTOracle.sol L313
rsETHPrice = newRsETHPrice;
```

The public entry point has no access control beyond `whenNotPaused`:

```solidity
// LRTOracle.sol L87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

The deposit path in `LRTDepositPool.getRsETHAmountToMint` reads the stored (potentially stale) price:

```solidity
// LRTDepositPool.sol L520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

The `instantWithdrawal` path reads the same stored price at the moment of the call via `getExpectedAssetAmount`:

```solidity
// LRTWithdrawalManager.sol L593
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

**Attack sequence (single transaction or same block):**
1. `rsETHPrice` is stale at `P_old`; true price (from accrued staking rewards) is `P_new > P_old`.
2. Attacker calls `depositETH(X ETH)` → receives `X / P_old` rsETH (inflated).
3. Attacker calls `updateRSETHPrice()` → `rsETHPrice` becomes `P_new`.
4. Attacker calls `instantWithdrawal(X / P_old rsETH)` → receives `(X / P_old) * P_new * (1 - fee)` ETH from `LRTUnstakingVault`.
5. Net profit ≈ `X * (P_new / P_old - 1)` ETH extracted from existing rsETH holders.

**Why existing guards are insufficient:**

The `pricePercentageLimit` check at `LRTOracle.sol` L252-266 reverts a non-manager caller only if the price increase *exceeds* the configured limit. It does not prevent the attack when the drift is within the limit — it merely caps the per-cycle profit. If `pricePercentageLimit == 0` (disabled), there is no cap at all.

The `_calculatePayoutAmount` min-guard used in queued withdrawals (`LRTWithdrawalManager.sol` L834) is absent from `instantWithdrawal`; the instant path pays out the full `getExpectedAssetAmount` at the current (just-updated) price with no cap at the deposited value.

The only other preconditions — `isInstantWithdrawalEnabled[asset] == true` and sufficient vault liquidity in `LRTUnstakingVault` — are both satisfied in production (instant withdrawal is a live feature with a dedicated fee recipient, and the vault holds assets returned from EigenLayer unstaking). The attacker's deposited ETH lands in `LRTDepositPool`, not the vault, so the vault liquidity is independent of the attack deposit.

## Impact Explanation
Each cycle extracts `X * (P_new/P_old - 1)` ETH from the protocol's TVL. This value belongs to existing rsETH holders (their principal + accrued yield). The attack is repeatable every time rewards accrue and the price has not been updated, and scales linearly with deposit size. This constitutes **Critical — direct theft of user funds** from the protocol TVL.

## Likelihood Explanation
- `updateRSETHPrice()` is callable by any unprivileged address with no cost beyond gas.
- `isInstantWithdrawalEnabled` is set to `true` for at least one asset in production (dedicated `instantWithdrawalFeeRecipient` is configured).
- EigenLayer staking rewards cause `rsETHPrice` to drift upward continuously; the attack window opens every time the price has not been updated recently.
- No deposit fee exists in `LRTDepositPool`, so there is zero cost to the attacker beyond gas.
- The `pricePercentageLimit` guard only limits per-cycle profit; it does not close the attack window.

## Recommendation
1. **Force a price update before minting**: Call `_updateRsETHPrice()` (or `updateRSETHPrice()`) at the start of `depositAsset` / `depositETH` so the deposit always uses the freshest price.
2. **Apply the same min-guard to `instantWithdrawal`**: Cap the payout at `min(depositPrice-based amount, currentPrice-based amount)`, analogous to `_calculatePayoutAmount` used in queued withdrawals.
3. **Add a deposit fee**: A deposit fee equal to or greater than the maximum expected price drift between updates would make the attack unprofitable.

## Proof of Concept
**Setup:**
- `rsETHPrice` (stored, stale) = `1.00 ETH` per rsETH
- True price after accrued rewards = `1.01 ETH` per rsETH
- `instantWithdrawalFee` = 10 bps (0.1%)
- `isInstantWithdrawalEnabled[ETH] = true`
- `LRTUnstakingVault` holds ≥ 101 ETH available for instant withdrawal

**Step 1 — Deposit at stale price (`LRTDepositPool.depositETH`):**
```
rsETH minted = 100 ETH / 1.00 = 100 rsETH
```

**Step 2 — Trigger price update (`LRTOracle.updateRSETHPrice`):**
```
rsETHPrice → 1.01 ETH  (within pricePercentageLimit, so non-manager call succeeds)
```

**Step 3 — Instant withdrawal (`LRTWithdrawalManager.instantWithdrawal`):**
```
assetAmountUnlocked = 100 rsETH * 1.01 ETH = 101 ETH
fee = 101 * 0.001 = 0.101 ETH
userAmount = 101 - 0.101 = 100.899 ETH
```

**Net profit = 100.899 − 100 = 0.899 ETH** extracted from existing rsETH holders in a single atomic sequence.

**Foundry test plan:**
1. Fork mainnet; deploy/configure contracts with `isInstantWithdrawalEnabled[ETH] = true` and a funded `LRTUnstakingVault`.
2. Warp time forward to allow staking rewards to accrue (or mock `_getTotalEthInProtocol` to return a value above `previousTVL`), leaving `rsETHPrice` stale.
3. From an attacker EOA: call `depositETH`, then `updateRSETHPrice`, then `instantWithdrawal` in sequence.
4. Assert attacker ETH balance after > attacker ETH balance before (net of gas).
5. Assert existing rsETH holders' redeemable value decreased by the extracted amount.