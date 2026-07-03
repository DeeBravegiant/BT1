Audit Report

## Title
`getExpectedAssetAmount` Returns Pre-Fee Amount, Overstating Instant Withdrawal Proceeds - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`getExpectedAssetAmount` is a public view function exposed via `ILRTWithdrawalManager` that callers use to preview how much underlying asset they will receive when burning rsETH. For the `instantWithdrawal` path, the function returns the gross oracle-derived amount with no awareness of `instantWithdrawalFee`, which is deducted inside `instantWithdrawal` after the fact. Any caller who previews via `getExpectedAssetAmount` before executing `instantWithdrawal` will receive `instantWithdrawalFee` basis points less than the advertised amount.

## Finding Description
`getExpectedAssetAmount` (L580–594) computes the gross conversion amount purely from oracle prices:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

No fee is subtracted. Inside `instantWithdrawal` (L228–252), the same gross value is obtained via `getExpectedAssetAmount`, and only then is the fee deducted:

```solidity
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked); // gross
...
uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
uint256 userAmount = assetAmountUnlocked - fee;
...
_transferAsset(asset, msg.sender, userAmount);
```

The user burns `rsETHUnstaked` in full but receives `userAmount`, which is strictly less than `getExpectedAssetAmount` returns whenever `instantWithdrawalFee > 0`. The emitted `AssetWithdrawalFinalized` event records `userAmount` (post-fee), confirming the gap. No existing check reconciles the view function's output with the actual transfer amount.

## Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**
Any off-chain UI, integration, or smart contract that calls `getExpectedAssetAmount` to preview an instant withdrawal will display or act on an inflated figure. The user burns the full `rsETHUnstaked` but receives `userAmount < getExpectedAssetAmount(asset, rsETHUnstaked)`. With `instantWithdrawalFee` configurable up to 1000 bps (10%), the discrepancy is material. No protocol funds are lost, but the contract's public interface misrepresents the actual return to the caller.

## Likelihood Explanation
`getExpectedAssetAmount` is part of the `ILRTWithdrawalManager` interface and is explicitly `public view`, making it the natural preview endpoint for any caller before executing `instantWithdrawal`. The condition is met whenever `instantWithdrawalFee > 0`, which is the normal operating state. Any unprivileged user calling the view function before withdrawing will encounter the discrepancy. No special access or unusual conditions are required.

## Recommendation
Add a dedicated instant-withdrawal preview function that deducts the fee:

```solidity
function getExpectedInstantWithdrawalAmount(address asset, uint256 rsETHAmount)
    external view returns (uint256 userAmount, uint256 fee)
{
    uint256 gross = getExpectedAssetAmount(asset, rsETHAmount);
    fee = (gross * instantWithdrawalFee) / 10_000;
    userAmount = gross - fee;
}
```

Alternatively, clearly document in the NatSpec of `getExpectedAssetAmount` that it returns the gross pre-fee amount and must not be used to estimate instant withdrawal proceeds.

## Proof of Concept
1. Deploy with `instantWithdrawalFee = 500` (5%).
2. Call `getExpectedAssetAmount(ETH, 1e18)` → returns `X` (e.g., `1.05e18`).
3. Call `instantWithdrawal(ETH, 1e18, "")`.
4. Inside `instantWithdrawal`: `assetAmountUnlocked = X`, `fee = X * 500 / 10_000`, `userAmount = X - fee`.
5. User receives `userAmount = 0.9975e18`, not `1.05e18` as advertised by `getExpectedAssetAmount`.
6. Confirm via the `AssetWithdrawalFinalized` event, which records `userAmount` (post-fee), not `assetAmountUnlocked`.

Foundry test plan: deploy `LRTWithdrawalManager`, set `instantWithdrawalFee = 500`, call `getExpectedAssetAmount` and record the return value, execute `instantWithdrawal`, assert that the actual ETH received by `msg.sender` equals `returnValue * 9500 / 10_000`, and assert it is strictly less than the `getExpectedAssetAmount` return value.