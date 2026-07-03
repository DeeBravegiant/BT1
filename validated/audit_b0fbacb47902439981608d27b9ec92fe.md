The full contract is now visible. Let me analyze each claim against the actual code.

**Key findings from `contracts/agETH/AGETHTokenWrapper.sol`:**

1. `removeAllowedToken` is guarded only by `DEFAULT_ADMIN_ROLE` — no timelock. [1](#0-0) 

2. `_withdraw` hard-reverts if `allowedTokens[_asset]` is false — no fallback path. [2](#0-1) 

3. There is **no `addAllowedToken` function** — the comment at line 153 explicitly states this is intentional. [3](#0-2) 

4. Compare with `RsETHTokenWrapper`, which uses `TIMELOCK_ROLE` for both `addAllowedToken` and `removeAllowedToken`, and has both functions. [4](#0-3) 

The `AGETHTokenWrapper` is missing all three safety mechanisms present in its sibling contract. Once `removeAllowedToken` is called, every wrapper token holder is permanently unable to redeem — there is no re-add path, no emergency sweep, and no alternative withdrawal route.

This is not an "admin compromise" scenario. The admin calling `removeAllowedToken` is a **legitimate, documented function** (e.g., to deprecate a bridge token version). The vulnerability is that this routine admin action has irreversible, catastrophic consequences for all existing holders with zero on-chain warning or recovery mechanism.

---

### Title
Permanent Freezing of All Wrapper Token Holders' Funds via `removeAllowedToken` with No Recovery Path — (`contracts/agETH/AGETHTokenWrapper.sol`)

### Summary
`AGETHTokenWrapper.removeAllowedToken` can be called by `DEFAULT_ADMIN_ROLE` at any time with no timelock. Once called, `_withdraw` permanently reverts for all holders of wrapper tokens backed by that asset. There is no `addAllowedToken` function, no emergency redemption path, and no on-chain warning mechanism.

### Finding Description
`_withdraw` enforces `if (!allowedTokens[_asset]) revert TokenNotAllowed()` before burning wrapper tokens or transferring the underlying asset back to the user. [5](#0-4) 

`removeAllowedToken` sets `allowedTokens[_asset] = false` with no timelock, no grace period, and no event that could trigger an off-chain warning before the state change takes effect. [1](#0-0) 

The contract explicitly omits `addAllowedToken` (line 153 comment), so the removal is irreversible on-chain. [3](#0-2) 

The sibling contract `RsETHTokenWrapper` correctly uses `TIMELOCK_ROLE` for both add and remove, providing a time window for users to exit before the change takes effect. [4](#0-3) 

### Impact Explanation
All holders of wrapper tokens backed by the removed asset lose the ability to redeem permanently. Their wrapper tokens become worthless ERC20 tokens with no on-chain redemption path. This is **Critical: Permanent freezing of funds**.

### Likelihood Explanation
The admin does not need to be malicious. A routine operational action — deprecating a bridge token version, migrating to a new bridge, or responding to a bridge exploit — would trigger this. The likelihood is **Medium**: the function exists for a reason and will plausibly be called during the protocol's lifetime.

### Recommendation
1. Add a `TIMELOCK_ROLE`-gated `addAllowedToken` function (matching `RsETHTokenWrapper`).
2. Gate `removeAllowedToken` behind `TIMELOCK_ROLE` with a meaningful delay (e.g., 48–72 hours) so existing holders can exit.
3. Alternatively, add an emergency `emergencyWithdraw` path that allows holders to redeem against any asset held by the contract regardless of `allowedTokens` state.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.27;

// Foundry test (local fork, no mainnet)
contract AGETHWrapperFreezeTest is Test {
    AGETHTokenWrapper wrapper;
    MockERC20 altAgETH;
    address admin = address(0xAD);
    address user  = address(0xBEEF);

    function setUp() public {
        altAgETH = new MockERC20();
        wrapper  = new AGETHTokenWrapper();
        wrapper.initialize(admin, admin, address(altAgETH));

        // Give user N altAgETH and let them deposit
        altAgETH.mint(user, 100e18);
        vm.prank(user);
        altAgETH.approve(address(wrapper), 100e18);
        vm.prank(user);
        wrapper.deposit(address(altAgETH), 100e18);
        // user now holds 100e18 wrapper tokens
    }

    function testPermanentFreeze() public {
        // Admin removes the only allowed token — no timelock
        vm.prank(admin);
        wrapper.removeAllowedToken(address(altAgETH));

        // User attempts to withdraw — reverts permanently
        vm.prank(user);
        vm.expectRevert(AGETHTokenWrapper.TokenNotAllowed.selector);
        wrapper.withdraw(address(altAgETH), 100e18);

        // No addAllowedToken exists — no recovery
        // wrapper.addAllowedToken(...) → compilation error: function does not exist
        // User's 100e18 wrapper tokens are permanently non-redeemable
    }
}
```

### Citations

**File:** contracts/agETH/AGETHTokenWrapper.sol (L111-116)
```text
    function _withdraw(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        _burn(msg.sender, _amount);

        ERC20Upgradeable(_asset).safeTransfer(_to, _amount);
```

**File:** contracts/agETH/AGETHTokenWrapper.sol (L153-153)
```text
    /// Dont' allow to add other tokens at the moment. Only allow the altAgETH token as set in the initialize function
```

**File:** contracts/agETH/AGETHTokenWrapper.sol (L157-160)
```text
    function removeAllowedToken(address _asset) external onlyRole(DEFAULT_ADMIN_ROLE) {
        allowedTokens[_asset] = false;
        emit TokenRemoved(_asset);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L174-185)
```text
    function addAllowedToken(address _asset) external onlyRole(TIMELOCK_ROLE) {
        _addAllowedToken(_asset);
    }

    /// @dev Remove a token from the allowed tokens list
    /// @param _asset The address of the token to remove
    function removeAllowedToken(address _asset) external onlyRole(TIMELOCK_ROLE) {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        allowedTokens[_asset] = false;
        emit TokenRemoved(_asset);
    }
```
