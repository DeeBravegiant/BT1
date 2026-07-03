Audit Report

## Title
`instantWithdrawal` Fee Bypassed via Integer Division Rounding to Zero for Dust Amounts - (File: contracts/LRTWithdrawalManager.sol)

## Summary
In `LRTWithdrawalManager.instantWithdrawal`, the protocol fee is computed via integer division that truncates to zero when `assetAmountUnlocked * instantWithdrawalFee < 10_000`. Because the `if (fee > 0)` guard then skips the fee transfer entirely, any rsETH holder can call `instantWithdrawal` with dust-sized inputs and receive the full asset amount with no fee deducted. The protocol fails to collect its intended instant-withdrawal fee on these calls.

## Finding Description
At `contracts/LRTWithdrawalManager.sol` L237–248, the fee is computed as:

```solidity
uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
uint256 userAmount = assetAmountUnlocked - fee;
...
if (fee > 0) {
    _transferAsset(asset, feeRecipient, fee);
    emit InstantWithdrawalFeeCollected(msg.sender, asset, fee);
}
```

When `assetAmountUnlocked * instantWithdrawalFee < 10_000`, Solidity truncates the division result to `0`. The `if (fee > 0)` guard then skips the fee transfer, and the user receives `assetAmountUnlocked` in full.

The minimum-amount guard at L224–226:
```solidity
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}
```
only rejects zero-value inputs when `minRsEthAmountToWithdraw[asset]` is at its default of `0` (uninitialized mapping, L35), so any non-zero rsETH amount passes.

`assetAmountUnlocked` is derived at L593 via `amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset)`, which approximates 1:1 for ETH/LSTs. At `instantWithdrawalFee = 1` (minimum 1 bps), any input below 10,000 wei produces `fee = 0`. The same rounding pattern exists in `RSETHPool.viewSwapRsETHAmountAndFee` (L312–313) and `RSETHPoolNoWrapper` (L278–279).

## Impact Explanation
The protocol fails to collect the instant-withdrawal fee for each dust-sized call. The fee avoided per call is at most `floor((9999 × instantWithdrawalFee) / 10_000) = 0` wei — sub-wei per transaction. No user principal is at risk and no funds are frozen. This matches **Low — Contract fails to deliver promised returns (fee collection) but does not lose value**.

## Likelihood Explanation
The exploit requires no special role — any rsETH holder can call `instantWithdrawal`. However, because all assets involved have 18 decimals, the per-call fee avoided is sub-wei (less than one wei). On Ethereum mainnet, gas costs dwarf any conceivable benefit, making the attack economically irrational. On low-cost L2s the economics improve marginally but remain impractical at scale. Likelihood is low.

## Recommendation
Replace the subtraction-based fee calculation with a formulation that rounds the user amount down (fee up), so the fee is never zero unless `instantWithdrawalFee == 0`:

```solidity
// Before (rounds fee down, can be 0):
uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
uint256 userAmount = assetAmountUnlocked - fee;

// After (rounds userAmount down, fee is never 0 unless instantWithdrawalFee == 0):
uint256 userAmount = assetAmountUnlocked * (10_000 - instantWithdrawalFee) / 10_000;
uint256 fee = assetAmountUnlocked - userAmount;
```

Apply the same fix to `viewSwapRsETHAmountAndFee` in `RSETHPool` and `RSETHPoolNoWrapper`. Additionally, set a non-zero `minRsEthAmountToWithdraw` for each supported asset to enforce a meaningful floor on withdrawal size.

## Proof of Concept
Assume `instantWithdrawalFee = 1` (0.01%), `minRsEthAmountToWithdraw[ETH] = 0` (default), rsETH price ≈ ETH price.

1. Attacker holds rsETH and calls `instantWithdrawal(ETH, 9_999, "")`.
2. `assetAmountUnlocked = 9_999 * rsETHPrice / assetPrice ≈ 9_999 wei`.
3. `fee = (9_999 * 1) / 10_000 = 0` (integer truncation at L237).
4. `if (fee > 0)` at L245 is false — fee transfer skipped.
5. `userAmount = 9_999 - 0 = 9_999 wei` — full amount returned, zero fee collected.
6. Repeat; each iteration avoids a fee that would have been `< 1 wei`.

Foundry fuzz test: fuzz `rsETHUnstaked` over `[1, 10_000 / instantWithdrawalFee - 1]` and assert `fee == 0` for all inputs in that range, confirming the rounding behavior is systematic.