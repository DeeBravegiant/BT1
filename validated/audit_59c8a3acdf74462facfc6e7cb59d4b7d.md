Audit Report

## Title
Missing Zero-Amount Guard Before rsETH Burn in `instantWithdrawal` Allows Dust rsETH to Be Destroyed for Zero Asset Return - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`LRTWithdrawalManager.instantWithdrawal` computes `assetAmountUnlocked` via integer division and immediately burns the caller's rsETH without first verifying that `assetAmountUnlocked > 0`. When `rsETHUnstaked * rsETHPrice < assetPrice`, the division truncates to zero, the rsETH is permanently destroyed, and the user receives no assets in return. The contract's only lower-bound guard (`minRsEthAmountToWithdraw[asset]`) defaults to zero and provides no protection.

## Finding Description
In `instantWithdrawal` (L212–253), the execution order is:

1. **L228** — `assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked)`, which computes `amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset)` (L593) using integer division.
2. **L229** — `IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked)` — rsETH is burned unconditionally, with no prior check that `assetAmountUnlocked > 0`.
3. **L237–238** — `fee` and `userAmount` are derived from `assetAmountUnlocked`; if it is zero, both are zero.
4. **L250** — `_transferAsset(asset, msg.sender, 0)` — user receives nothing.

The only pre-burn guard is:
```solidity
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}
```
`minRsEthAmountToWithdraw` is declared as `mapping(address asset => uint256)` (L35) and defaults to `0`, so the effective lower bound is `rsETHUnstaked >= 1`. A call with `rsETHUnstaked = 1` passes this check. If the target asset's oracle price exceeds `rsETHPrice` (e.g., a newly supported high-value LST), the division `1 * rsETHPrice / assetPrice` truncates to zero, the burn executes, and the user receives nothing.

## Impact Explanation
The contract fails to deliver its promised return: the user burns rsETH and receives zero underlying assets. This maps to **Low — Contract fails to deliver promised returns**. The practical loss per call is bounded to dust amounts (only values of `rsETHUnstaked` small enough to cause truncation are affected), but the rsETH principal is permanently destroyed rather than merely withheld.

## Likelihood Explanation
Under the current asset set (ETH, stETH, ETHx — all priced below rsETH), truncation to zero is not reachable for any non-trivial input. The condition becomes reachable if: (1) a new asset is added whose oracle price exceeds `rsETHPrice`, and (2) `minRsEthAmountToWithdraw[asset]` is left at its default of `0`. Both are plausible during normal protocol expansion. An unprivileged user triggers the loss with a single public call to `instantWithdrawal`. The loss per call is dust-level but repeatable.

## Recommendation
Add an explicit zero-amount guard immediately after computing `assetAmountUnlocked`, before the burn:

```solidity
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
if (assetAmountUnlocked == 0) revert InvalidAmountToWithdraw();
IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
```

Additionally, enforce a non-zero `minRsEthAmountToWithdraw[asset]` as a precondition before enabling instant withdrawal for any asset, so that integer-division truncation to zero is structurally impossible for any enabled asset.

## Proof of Concept
1. Admin adds a new LST with oracle price `2e18` (2 ETH/token); `rsETHPrice = 1.05e18`.
2. Admin enables instant withdrawal for the asset but leaves `minRsEthAmountToWithdraw[asset] = 0` (default).
3. User calls `instantWithdrawal(asset, 1, "")` (1 wei rsETH).
4. `getExpectedAssetAmount`: `1 * 1.05e18 / 2e18 = 0` (integer truncation). ← L593
5. `burnFrom(user, 1)` executes — 1 wei rsETH destroyed. ← L229
6. `assetAmountUnlocked = 0` → `fee = 0`, `userAmount = 0`.
7. `_transferAsset(asset, msg.sender, 0)` — user receives nothing. ← L250
8. Net: user loses 1 wei rsETH, receives zero assets.

**Foundry fuzz test sketch:**
```solidity
function testFuzz_instantWithdrawalZeroReturn(uint256 rsETHUnstaked) public {
    vm.assume(rsETHUnstaked > 0 && rsETHUnstaked < assetPrice / rsETHPrice);
    // setup: asset oracle price > rsETHPrice, minRsEthAmountToWithdraw = 0
    uint256 balanceBefore = rsETH.balanceOf(user);
    vm.prank(user);
    withdrawalManager.instantWithdrawal(asset, rsETHUnstaked, "");
    assertEq(rsETH.balanceOf(user), balanceBefore - rsETHUnstaked); // rsETH burned
    assertEq(IERC20(asset).balanceOf(user), 0);                     // nothing received
}
```