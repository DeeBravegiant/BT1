Audit Report

## Title
`getRsETHAmountToMint` Returns Non-Zero Exchange Data for Removed Assets, Inconsistent with Deposit Revert Behavior - (File: contracts/LRTDepositPool.sol)

## Summary
`LRTDepositPool.getRsETHAmountToMint` computes a rsETH mint amount purely from oracle data without checking whether the asset is currently supported. Because `LRTConfig.removeSupportedAsset` deletes `isSupportedAsset[asset]` but does not clear `LRTOracle.assetPriceOracle[asset]`, the view function continues to return a non-zero, plausible-looking rsETH amount for a removed asset, while any actual deposit attempt for that asset reverts. This creates a concrete inconsistency between the view function's implied promise and the protocol's actual behavior.

## Finding Description
`getRsETHAmountToMint` at `contracts/LRTDepositPool.sol` L506–521 performs no `isSupportedAsset` check:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`lrtOracle.getAssetPrice` is gated only by `onlySupportedOracle` (`contracts/LRTOracle.sol` L40–45), which reverts only if `assetPriceOracle[asset] == address(0)`. It does not consult `lrtConfig.isSupportedAsset`.

`LRTConfig.removeSupportedAsset` (`contracts/LRTConfig.sol` L66–93) deletes `isSupportedAsset[asset]` and `assetStrategy[asset]` but never touches `LRTOracle.assetPriceOracle[asset]`. After removal, the oracle mapping entry remains non-zero.

`updatePriceOracleFor` (`contracts/LRTOracle.sol` L113–119) only enforces a non-zero oracle address when `lrtConfig.isSupportedAsset(asset)` is true; it does not prevent the stale entry from persisting after removal.

Meanwhile, `depositAsset` (`contracts/LRTDepositPool.sol` L99–118) is guarded by `onlySupportedERC20Token(asset)`, which reverts for any asset not in the supported list.

Exploit flow:
1. Admin adds `tokenX`, sets its oracle in `LRTOracle`.
2. Admin calls `LRTConfig.removeSupportedAsset(tokenX, idx)` — `isSupportedAsset[tokenX]` is deleted; `assetPriceOracle[tokenX]` is not cleared.
3. Any user calls `getRsETHAmountToMint(tokenX, 1 ether)` — succeeds, returns a non-zero rsETH amount.
4. User calls `depositAsset(tokenX, 1 ether, ...)` — reverts with `AssetNotSupported`.

## Impact Explanation
The view function `getRsETHAmountToMint` implicitly promises that depositing the queried asset will yield the returned rsETH amount. After asset removal, this promise is broken: the view returns a valid-looking non-zero value, but the deposit reverts. No funds are lost or frozen, but the contract fails to deliver the return implied by the public view function. This matches the allowed Low impact: **Contract fails to deliver promised returns, but doesn't lose value.**

## Likelihood Explanation
The precondition is a normal admin lifecycle operation (`removeSupportedAsset`), not an attacker-controlled action. Once triggered, any unprivileged external caller can invoke the public view function with the removed asset and observe the inconsistency. No special permissions, timing, or attacker capability are required beyond the prior admin removal event.

## Recommendation
Add an `isSupportedAsset` guard to `getRsETHAmountToMint`, consistent with the deposit functions:

```solidity
function getRsETHAmountToMint(address asset, uint256 amount)
    public
    view
    override
    onlySupportedAsset(asset)
    returns (uint256 rsethAmountToMint)
{
    ...
}
```

Alternatively, have `LRTConfig.removeSupportedAsset` call `LRTOracle.updatePriceOracleFor(asset, address(0))` to clear the stale oracle entry as part of the removal flow.

## Proof of Concept
1. Deploy the protocol; add `tokenX` as a supported asset and configure its price oracle in `LRTOracle`.
2. Call `LRTConfig.removeSupportedAsset(tokenX, idx)`. Verify `isSupportedAsset[tokenX] == false` and `LRTOracle.assetPriceOracle[tokenX] != address(0)`.
3. Call `LRTDepositPool.getRsETHAmountToMint(tokenX, 1 ether)`. Observe it returns a non-zero value (e.g., `0.95 ether`).
4. Call `LRTDepositPool.depositAsset(tokenX, 1 ether, 0, "")`. Observe it reverts with `AssetNotSupported`.
5. The view function and deposit function are inconsistent: step 3 implies the deposit is viable; step 4 proves it is not.