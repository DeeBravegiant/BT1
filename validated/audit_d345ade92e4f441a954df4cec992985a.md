Audit Report

## Title
Cross-Token Drain via Broken Per-Asset Capacity Check in `depositBridgerAssets` — (`contracts/L2/RsETHTokenWrapper.sol`)

## Summary

`maxAmountToDepositBridgerAsset` computes available bridger capacity for a given asset by subtracting only that asset's own balance from `totalSupply()`, ignoring all other allowed tokens already held by the wrapper. This allows the bridger to deposit tokenB even when `totalSupply` is already fully backed by tokenA. Any wrsETH holder can then call `withdraw(tokenB, N)`, draining tokenB while leaving tokenA permanently stranded with `totalSupply == 0` and no redemption path.

## Finding Description

`maxAmountToDepositBridgerAsset` at L99–110 of `contracts/L2/RsETHTokenWrapper.sol` is the sole guard on `depositBridgerAssets`:

```solidity
uint256 wrsETHSupply = totalSupply();
uint256 balanceOfAssetInWrapper = ERC20Upgradeable(_asset).balanceOf(address(this));
if (balanceOfAssetInWrapper > wrsETHSupply) return 0;
return wrsETHSupply - balanceOfAssetInWrapper;   // only checks _asset's own balance
```

It does not subtract the balances of other allowed tokens. When tokenA already fully backs `totalSupply = N`, the formula for tokenB returns `N − 0 = N`, signalling full capacity where none exists.

`_deposit` (L134–141) mints wrsETH 1:1 against any allowed token with no record of which token backs which shares. `_withdraw` (L120–128) burns wrsETH and transfers any allowed token, also with no per-token accounting. Together, these allow cross-token redemption.

Exploit sequence:
1. User deposits N tokenA → N wrsETH minted. State: `totalSupply=N`, `tokenA.bal=N`, `tokenB.bal=0`.
2. Bridger calls `depositBridgerAssets(tokenB, N)`. The capacity check returns `N − 0 = N`, so it passes. State: `totalSupply=N`, `tokenA.bal=N`, `tokenB.bal=N`.
3. User calls `withdraw(tokenB, N)`. Burns N wrsETH, transfers N tokenB. State: `totalSupply=0`, `tokenA.bal=N`, `tokenB.bal=0`.

tokenA is now permanently locked: `withdraw`/`withdrawTo` require burning wrsETH (none exists), and `depositBridgerAssets` only deposits. There is no rescue path.

## Impact Explanation

**Critical — Permanent freezing of funds.** After the exploit, N tokenA is irrecoverably locked in the wrapper contract. No function in the contract can release ERC-20 tokens without burning wrsETH, and `totalSupply == 0` means no wrsETH exists to burn. The invariant `sum(allowedToken balances) ≤ totalSupply()` is broken, and the excess collateral is frozen forever.

## Likelihood Explanation

Both preconditions are part of normal protocol operation. The contract explicitly supports multiple allowed tokens via `addAllowedToken` (L174) and `reinitialize` (L47). The bridger's deposit of tokenB is the intended workflow for collateralizing pre-minted wrsETH on L2; the bridger relies on the contract's own `maxAmountToDepositBridgerAsset` check to determine capacity, which incorrectly reports full capacity. The final drain step requires no special role — any wrsETH holder can call the public `withdraw` function. The bridger is not acting maliciously; the broken accounting check is what enables the over-deposit.

## Recommendation

Replace the per-asset capacity check with a global one that accounts for all allowed token balances:

```solidity
// Requires an enumerable list of allowed tokens (currently only a mapping exists)
uint256 totalBacking = 0;
for (uint i = 0; i < allowedTokenList.length; i++) {
    totalBacking += ERC20Upgradeable(allowedTokenList[i]).balanceOf(address(this));
}
return totalSupply() > totalBacking ? totalSupply() - totalBacking : 0;
```

This requires adding an enumerable `allowedTokenList` array alongside the existing `allowedTokens` mapping. Alternatively, enforce that only one token may be active at a time, or track per-token deposits and restrict withdrawals to the same token that was deposited.

## Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.27;

function test_crossTokenDrain() public {
    uint256 N = 1e18;

    // Step 1: user deposits N tokenA → receives N wrsETH
    tokenA.mint(user, N);
    vm.prank(user); tokenA.approve(address(wrapper), N);
    vm.prank(user); wrapper.deposit(address(tokenA), N);
    // totalSupply=N, tokenA.bal=N, tokenB.bal=0

    // Step 2: bridger deposits N tokenB
    // maxAmountToDepositBridgerAsset(tokenB) = N - 0 = N  ← incorrectly passes
    tokenB.mint(bridger, N);
    vm.prank(bridger); tokenB.approve(address(wrapper), N);
    vm.prank(bridger); wrapper.depositBridgerAssets(address(tokenB), N);
    // totalSupply=N, tokenA.bal=N, tokenB.bal=N

    // Step 3: user drains tokenB
    vm.prank(user); wrapper.withdraw(address(tokenB), N);
    // totalSupply=0, tokenA.bal=N, tokenB.bal=0

    // Invariant broken: tokenA permanently stranded
    assertEq(wrapper.totalSupply(), 0);
    assertEq(tokenA.balanceOf(address(wrapper)), N);
}
```

The broken invariant `tokenA.balanceOf(wrapper) + tokenB.balanceOf(wrapper) ≤ totalSupply()` is violated: `N + 0 > 0`. tokenA is permanently frozen with no redemption path.