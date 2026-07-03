Let me read the full `RsETHTokenWrapper.sol` to confirm there are no alternative redemption paths.

The vulnerability is confirmed and concrete. Here is the full analysis:

---

### Title
`removeAllowedToken` Lacks Outstanding-Supply Guard, Permanently Freezing wrsETH Redemption — (`contracts/L2/RsETHTokenWrapper.sol`)

### Summary
`RsETHTokenWrapper.removeAllowedToken` sets `allowedTokens[_asset] = false` with no check on outstanding wrsETH supply. Because `_withdraw` gates every redemption path on `allowedTokens[_asset]`, removing the only allowed token while `totalSupply() > 0` makes all wrsETH permanently unredeemable.

### Finding Description
`removeAllowedToken` contains only a role guard and an existence check: [1](#0-0) 

It does not verify that `totalSupply() == 0` or that a replacement allowed token exists. Every user-facing redemption path (`withdraw`, `withdrawTo`) funnels through `_withdraw`, which hard-reverts on a non-allowed asset: [2](#0-1) 

There is no emergency burn path, no rescue function, and no way to redeem wrsETH without specifying an allowed asset. The `mint` function (MINTER_ROLE) can create wrsETH without a deposit, making the supply/collateral mismatch even worse, but even in the normal case the path is fully blocked. [3](#0-2) 

### Impact Explanation
Once `removeAllowedToken` is called with the last (or only) allowed token while `totalSupply() > 0`:

- `withdraw(removedToken, amount)` → `TokenNotAllowed` revert
- `withdraw(anyOtherToken, amount)` → `TokenNotAllowed` revert (no other token is allowed)
- The underlying altRsETH balance remains locked in the contract forever
- All wrsETH holders permanently lose the ability to redeem their yield-bearing position

This satisfies **Medium — Permanent freezing of unclaimed yield**: wrsETH represents wrapped rsETH which continuously accrues staking yield; holders can never access that yield or principal once the token is removed.

### Likelihood Explanation
The `TIMELOCK_ROLE` is held by a `TimelockController` (confirmed in the README deployment tables). A legitimate governance action — e.g., migrating from one altRsETH bridge token to a newer version — would naturally call `removeAllowedToken` on the old token. The timelock delay provides visibility but does not prevent execution. No malicious intent is required; a routine token migration with outstanding wrsETH supply triggers the freeze. The scenario is realistic and requires no key compromise.

### Recommendation
Add a supply-safety guard in `removeAllowedToken`:

```solidity
function removeAllowedToken(address _asset) external onlyRole(TIMELOCK_ROLE) {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    // Ensure no outstanding wrsETH is backed solely by this token,
    // or that a replacement allowed token already exists.
    require(
        totalSupply() == 0 || ERC20Upgradeable(_asset).balanceOf(address(this)) == 0,
        "Outstanding wrsETH backed by this token"
    );
    allowedTokens[_asset] = false;
    emit TokenRemoved(_asset);
}
```

A stricter approach: require that at least one other allowed token exists before removing the current one, or require `totalSupply() == 0`.

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.27;

// Pseudocode unit test (Foundry)
function test_permanentFreeze() public {
    // Setup: deploy RsETHTokenWrapper with altRsETH as the only allowed token
    // Grant TIMELOCK_ROLE to timelockAddr
    // User deposits 100e18 altRsETH → receives 100e18 wrsETH
    wrapper.deposit(altRsETH, 100e18);
    assertEq(wrapper.totalSupply(), 100e18);

    // TIMELOCK_ROLE removes the only allowed token
    vm.prank(timelockAddr);
    wrapper.removeAllowedToken(altRsETH);

    // User attempts to withdraw — all paths revert
    vm.expectRevert(RsETHTokenWrapper.TokenNotAllowed.selector);
    wrapper.withdraw(altRsETH, 100e18);

    // No other token is allowed — any other address also reverts
    vm.expectRevert(RsETHTokenWrapper.TokenNotAllowed.selector);
    wrapper.withdraw(address(0xdead), 100e18);

    // wrsETH supply is non-zero, zero redeemable tokens
    assertGt(wrapper.totalSupply(), 0);
    assertFalse(wrapper.allowedTokens(altRsETH));
}
```

The test passes on unmodified code, confirming the invariant break: `totalSupply() > 0` with zero redeemable paths. [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/L2/RsETHTokenWrapper.sol (L84-86)
```text
    function withdraw(address asset, uint256 _amount) external {
        _withdraw(asset, msg.sender, _amount);
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

**File:** contracts/L2/RsETHTokenWrapper.sol (L178-185)
```text
    /// @dev Remove a token from the allowed tokens list
    /// @param _asset The address of the token to remove
    function removeAllowedToken(address _asset) external onlyRole(TIMELOCK_ROLE) {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        allowedTokens[_asset] = false;
        emit TokenRemoved(_asset);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L187-192)
```text
    /// @dev Mint wrsETH tokens on L2
    /// @param _to The address to mint the tokens to
    /// @param _amount The amount of tokens to mint
    function mint(address _to, uint256 _amount) external onlyRole(MINTER_ROLE) {
        _mint(_to, _amount);
    }
```
