Audit Report

## Title
Cross-Token Cap Miscalculation Allows Bridger to Deposit Redundant Collateral, Permanently Locking Funds — (`contracts/L2/RsETHTokenWrapper.sol`)

## Summary
`maxAmountToDepositBridgerAsset(_asset)` computes the available cap as `totalSupply() - balanceOf(_asset)`. When multiple tokens are allowed and the wrsETH supply is already fully backed by a second token, this formula returns a non-zero cap for the first token, permitting the bridger to deposit it even though no additional collateral is needed. Because `depositBridgerAssets` transfers tokens in without minting wrsETH, and the only withdrawal path (`_withdraw`) requires burning wrsETH, the deposited tokens become unrecoverable until an admin performs an out-of-band `mint`.

## Finding Description
`RsETHTokenWrapper` supports multiple allowed tokens via `addAllowedToken` (TIMELOCK_ROLE), and `reinitialize` already calls `_addAllowedToken` for a second token, confirming multi-token operation is an intended deployment scenario.

The cap formula at line 109:
```solidity
return wrsETHSupply - balanceOfAssetInWrapper;
```
uses `totalSupply()` — which aggregates wrsETH minted from **all** allowed tokens — but subtracts only the balance of the single queried token. When a second token has already fully backed the supply, the formula returns `N - 0 = N` instead of `0`.

Exploit path:
1. `userB.deposit(tokenB, N)` → `totalSupply = N`, `wrapper.tokenB = N`
2. `maxAmountToDepositBridgerAsset(tokenA)` returns `N - 0 = N` (should be `0`)
3. Bridger calls `depositBridgerAssets(tokenA, N)` → N tokenA transferred in, **no wrsETH minted** (lines 162–170 contain no `_mint` call)
4. `userB.withdraw(tokenB, N)` → burns N wrsETH, `totalSupply = 0`
5. Wrapper holds N tokenA; `totalSupply = 0`; `_withdraw(tokenA, ...)` requires burning wrsETH that no longer exists (line 123: `_burn(msg.sender, _amount)`)

There is no `withdrawBridgerAssets` or emergency recovery function. The only exit is an admin calling `mint` (MINTER_ROLE, line 190) to synthetically recreate wrsETH for the bridger to burn — an out-of-band action not part of normal operation.

## Impact Explanation
The bridger's tokenA is frozen in the wrapper with no on-chain recovery path available to any normal participant. Recovery requires privileged admin intervention via `mint`. This matches **Medium — Temporary freezing of funds** (permanent absent admin action, but the `mint` function does provide an administrative escape hatch).

## Likelihood Explanation
No malicious actor is required. The bridger acts in good faith: it queries `maxAmountToDepositBridgerAsset` and receives a non-zero cap, then deposits accordingly. Multi-token operation is an intended and already-deployed scenario (`reinitialize` adds a second allowed token). The bridger has no way to detect that the supply is already fully backed by a different token. The condition is reachable in normal protocol operation whenever two allowed tokens are active simultaneously.

## Recommendation
Replace the single-asset cap formula with one that sums the balances of all allowed tokens:

```solidity
// Maintain an EnumerableSet of allowed tokens
uint256 totalBacking = 0;
for (uint256 i = 0; i < allowedTokensList.length(); i++) {
    totalBacking += ERC20Upgradeable(allowedTokensList.at(i)).balanceOf(address(this));
}
return wrsETHSupply > totalBacking ? wrsETHSupply - totalBacking : 0;
```

This requires replacing the `mapping(address => bool) allowedTokens` with an `EnumerableSet.AddressSet` (or maintaining a parallel array) so the sum can be computed on-chain. The per-asset cap check in `maxAmountToDepositBridgerAsset` should then use this aggregate backing rather than the single-token balance.

## Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.27;

function test_crossTokenCapMiscalculation() public {
    // Setup: wrapper already has tokenA; add tokenB
    wrapper.addAllowedToken(address(tokenB)); // TIMELOCK_ROLE

    uint256 N = 1000e18;

    // Step 1: userB deposits N tokenB → mints N wrsETH
    tokenB.mint(userB, N);
    vm.prank(userB); tokenB.approve(address(wrapper), N);
    vm.prank(userB); wrapper.deposit(address(tokenB), N);
    assertEq(wrapper.totalSupply(), N);

    // Step 2: cap for tokenA should be 0 (supply fully backed by tokenB)
    // BUG: returns N instead of 0
    assertEq(wrapper.maxAmountToDepositBridgerAsset(address(tokenA)), N);

    // Step 3: bridger deposits N tokenA (passes cap check, no wrsETH minted)
    tokenA.mint(bridger, N);
    vm.prank(bridger); tokenA.approve(address(wrapper), N);
    vm.prank(bridger); wrapper.depositBridgerAssets(address(tokenA), N);

    // Step 4: userB withdraws tokenB — burns all wrsETH
    vm.prank(userB); wrapper.withdraw(address(tokenB), N);

    // Result: N tokenA locked, totalSupply = 0, no recovery path
    assertEq(tokenA.balanceOf(address(wrapper)), N);  // locked
    assertEq(wrapper.totalSupply(), 0);               // no wrsETH to burn

    // Confirm withdraw(tokenA, N) is impossible — would revert on _burn
    vm.expectRevert();
    vm.prank(bridger); wrapper.withdraw(address(tokenA), N);
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/L2/RsETHTokenWrapper.sol (L47-49)
```text
    function reinitialize(address _altRsETH) external reinitializer(2) onlyRole(DEFAULT_ADMIN_ROLE) {
        _addAllowedToken(_altRsETH);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L99-110)
```text
    function maxAmountToDepositBridgerAsset(address _asset) public view returns (uint256) {
        if (!allowedTokens[_asset]) return 0;

        // get totalSupply of wrsETH minted
        uint256 wrsETHSupply = totalSupply();
        // balance of _asset with the contract
        uint256 balanceOfAssetInWrapper = ERC20Upgradeable(_asset).balanceOf(address(this));

        if (balanceOfAssetInWrapper > wrsETHSupply) return 0;

        return wrsETHSupply - balanceOfAssetInWrapper;
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L120-128)
```text
    function _withdraw(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        _burn(msg.sender, _amount);

        ERC20Upgradeable(_asset).safeTransfer(_to, _amount);

        emit Withdraw(_asset, msg.sender, _to, _amount);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L162-170)
```text
    function depositBridgerAssets(address _asset, uint256 _amount) external onlyRole(BRIDGER_ROLE) {
        if (maxAmountToDepositBridgerAsset(_asset) < _amount) {
            revert CannotDeposit();
        }

        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        emit BridgerDeposited(_asset, msg.sender, _amount);
    }
```
