Audit Report

## Title
`getExpectedAssetAmount` Overstates Instant Withdrawal Proceeds When `instantWithdrawalFee > 0` - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`getExpectedAssetAmount` is documented as returning "Amount of underlying to receive" but computes only the gross oracle-derived amount, never deducting `instantWithdrawalFee`. When `instantWithdrawalFee > 0`, `instantWithdrawal` transfers `assetAmountUnlocked - fee` to the caller, not `assetAmountUnlocked`. Any integrator or contract that previews proceeds via `getExpectedAssetAmount` before calling `instantWithdrawal` will receive up to 10% less than the value returned by the view function.

## Finding Description
`getExpectedAssetAmount` (L580–594) returns:
```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```
This is the gross amount with no fee adjustment. `instantWithdrawal` (L228) calls this same function to obtain `assetAmountUnlocked`, then at L237–238 computes:
```solidity
uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
uint256 userAmount = assetAmountUnlocked - fee;
```
and transfers only `userAmount` to `msg.sender` at L250. The fee is silently withheld. No existing check in `getExpectedAssetAmount` or in the `ILRTWithdrawalManager` interface signals this deduction. `setInstantWithdrawalFee` (L372–374) permits any value up to 1000 bps, so the discrepancy scales linearly with the configured fee.

## Impact Explanation
This matches **Low — Contract fails to deliver promised returns, but doesn't lose value**. The public view function, which is the natural preview entry point for integrators, promises a return value that the contract does not honour on the instant-withdrawal path. A downstream contract that enforces a minimum-output invariant (e.g., flash-loan repayment, slippage guard) based on `getExpectedAssetAmount` will receive less than expected and may revert or misbehave. No user funds are lost beyond the legitimate fee, so higher severity is not warranted.

## Likelihood Explanation
The condition requires only that `instantWithdrawalFee` be set to any non-zero value by the LRT manager — the normal operational state for a fee-bearing feature. Once set, every call to `instantWithdrawal` by any unprivileged user triggers the discrepancy. The function is part of the public `ILRTWithdrawalManager` interface, making it the expected preview mechanism for integrators. No special attacker capability is needed.

## Recommendation
Add a dedicated `getExpectedInstantWithdrawalAmount` view function that applies the fee deduction:
```solidity
function getExpectedInstantWithdrawalAmount(address asset, uint256 amount)
    public view returns (uint256)
{
    uint256 gross = getExpectedAssetAmount(asset, amount);
    uint256 fee = (gross * instantWithdrawalFee) / 10_000;
    return gross - fee;
}
```
Alternatively, update `getExpectedAssetAmount`'s NatSpec to clearly state it returns the gross pre-fee amount, and expose the net function separately so integrators are not misled.

## Proof of Concept
1. LRT manager calls `setInstantWithdrawalFee(500)` (5%).
2. Integrator calls `getExpectedAssetAmount(stETH, 1e18)` → returns `X`.
3. Integrator calls `instantWithdrawal(stETH, 1e18, "")` expecting to receive `X`.
4. At L237–238, `fee = X * 500 / 10_000 = X * 0.05`; `userAmount = X * 0.95`.
5. `_transferAsset` at L250 sends only `X * 0.95` to the integrator.
6. Any downstream logic asserting `received >= X` fails.

Foundry test sketch:
```solidity
uint256 preview = manager.getExpectedAssetAmount(stETH, 1e18);
uint256 balBefore = IERC20(stETH).balanceOf(address(this));
manager.instantWithdrawal(stETH, 1e18, "");
uint256 received = IERC20(stETH).balanceOf(address(this)) - balBefore;
assertEq(received, preview); // fails when instantWithdrawalFee > 0
```