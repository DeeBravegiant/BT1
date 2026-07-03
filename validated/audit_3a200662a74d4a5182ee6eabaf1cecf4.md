Audit Report

## Title
`getExpectedAssetAmount` Returns Pre-Fee Gross Amount, Overstating Instant Withdrawal Proceeds - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`getExpectedAssetAmount` is a public view function exposed in `ILRTWithdrawalManager` that callers use to preview how much underlying asset they will receive when burning rsETH. For the `instantWithdrawal` path, the function returns the gross oracle-derived amount with no awareness of `instantWithdrawalFee`, which is deducted inside `instantWithdrawal` after the fact. Any caller previewing an instant withdrawal via `getExpectedAssetAmount` will receive a value that is `instantWithdrawalFee` basis points higher than what is actually transferred.

## Finding Description
`getExpectedAssetAmount` at `contracts/LRTWithdrawalManager.sol` L580–594 computes the gross asset amount purely from oracle prices:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

Inside `instantWithdrawal` (L228–252), this gross value is used as the basis for a fee deduction that is never reflected in the view function:

```solidity
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked); // gross
...
uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
uint256 userAmount = assetAmountUnlocked - fee;
...
_transferAsset(asset, msg.sender, userAmount); // user receives less than getExpectedAssetAmount
```

`getExpectedAssetAmount` is declared in `ILRTWithdrawalManager` (L81) as an `external view` function explicitly intended for external consumption. Its NatDoc states "Get asset amount to receive when trading in rsETH," implying it should reflect what the user actually receives. No existing guard or documentation corrects this expectation. The emitted `AssetWithdrawalFinalized` event records `userAmount` (post-fee), confirming the discrepancy between the view function output and the actual transfer.

## Impact Explanation
Any off-chain integration, UI, or smart contract that calls `getExpectedAssetAmount` to preview an instant withdrawal will act on an inflated figure. The user burns the full `rsETHUnstaked` but receives `userAmount < getExpectedAssetAmount(asset, rsETHUnstaked)`. No protocol funds are lost, but the contract fails to deliver the amount advertised through its public interface.

**Severity: Low** — Contract fails to deliver promised returns, but doesn't lose value.

## Likelihood Explanation
`getExpectedAssetAmount` is part of the `ILRTWithdrawalManager` interface and is the natural preview function for any caller before executing `instantWithdrawal`. No privileged access is required. The discrepancy is triggered by any unprivileged user performing an instant withdrawal when `instantWithdrawalFee > 0`. The fee can be set up to a material level (the contract enforces a `FeeTooHigh` guard), making the gap non-trivial.

## Recommendation
Add a dedicated view function for instant withdrawal previews that deducts the fee:

```solidity
function getExpectedInstantWithdrawalAmount(address asset, uint256 rsETHAmount)
    external view returns (uint256 userAmount, uint256 fee)
{
    uint256 gross = getExpectedAssetAmount(asset, rsETHAmount);
    fee = (gross * instantWithdrawalFee) / 10_000;
    userAmount = gross - fee;
}
```

Alternatively, update the NatDoc on `getExpectedAssetAmount` to explicitly state it returns the gross pre-fee amount and must not be used to estimate instant withdrawal proceeds, and expose the new function in `ILRTWithdrawalManager`.

## Proof of Concept
1. Deploy with `instantWithdrawalFee = 500` (5%).
2. Call `getExpectedAssetAmount(ETH, 1e18)` → returns `X` (e.g., `1.05e18`).
3. Call `instantWithdrawal(ETH, 1e18, "")`.
4. Inside `instantWithdrawal`: `assetAmountUnlocked = X`, `fee = X * 500 / 10_000`, `userAmount = X - fee`.
5. `_transferAsset` sends `userAmount` to the caller — strictly less than `X` returned by step 2.
6. `AssetWithdrawalFinalized` emits `userAmount`, confirming the shortfall vs. the view function's output.

Foundry test plan: fork mainnet, set `instantWithdrawalFee` to a non-zero value, call `getExpectedAssetAmount` and record the return value, execute `instantWithdrawal` with the same parameters, assert that the actual ETH/LST balance increase equals `userAmount` and is strictly less than the recorded `getExpectedAssetAmount` return value.