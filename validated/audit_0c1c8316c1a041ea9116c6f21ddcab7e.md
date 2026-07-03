Audit Report

## Title
Bridge-Minted Wrapper Tokens Drain Deposit-Backed altAgETH via Undifferentiated `_withdraw()` — (`contracts/agETH/AGETHTokenWrapper.sol`)

## Summary
`AGETHTokenWrapper` issues fungible wrapper tokens via two paths: `_deposit()` (backed 1:1 by altAgETH transferred into the contract) and `mint()` (no collateral, intended for bridge/L2 use). `_withdraw()` burns any wrapper token and transfers altAgETH from the contract's balance with no distinction between the two origins and no invariant enforcing `balanceOf(contract) >= totalSupply()`. Any recipient of bridge-minted tokens can immediately redeem them against altAgETH deposited by regular users, constituting direct theft of funds at rest.

## Finding Description
`mint()` (L165–167) calls `_mint()` directly with no altAgETH transfer into the contract:

```solidity
function mint(address _to, uint256 _amount) external onlyRole(MINTER_ROLE) {
    _mint(_to, _amount);
}
```

`_withdraw()` (L111–119) burns wrapper tokens from `msg.sender` and unconditionally transfers altAgETH from the contract balance:

```solidity
function _withdraw(address _asset, address _to, uint256 _amount) internal {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    _burn(msg.sender, _amount);
    ERC20Upgradeable(_asset).safeTransfer(_to, _amount);
    emit Withdraw(_asset, _to, _amount);
}
```

The backing mechanism `depositBridgerAssets()` (L143–151) is a separate, role-gated call with no atomic coupling to `mint()`. `maxAmountToDepositBridgerAsset()` (L90–101) is only consulted inside `depositBridgerAssets()` — it is never checked in `_withdraw()`. There is no guard anywhere that enforces `altAgETH.balanceOf(address(this)) >= totalSupply()` before a withdrawal proceeds.

Because both paths produce identical ERC-20 tokens, the exploit requires no privileged action beyond the bridge performing its intended function:

1. User A calls `deposit(altAgETH, 100e18)` → contract holds 100e18 altAgETH, `totalSupply = 100e18`.
2. Bridge (MINTER_ROLE) calls `mint(UserB, 100e18)` — normal bridge operation → contract still holds 100e18 altAgETH, `totalSupply = 200e18`.
3. User B (unprivileged) calls `withdraw(altAgETH, 100e18)` → burns 100e18 wrapper tokens, receives 100e18 altAgETH.
4. Contract holds 0 altAgETH; User A's 100e18 wrapper tokens are permanently unbacked.

Existing checks are insufficient: the `allowedTokens` guard only validates the asset address; there is no per-token-origin accounting, no supply/balance invariant, and no restriction on which wrapper tokens may be used to redeem altAgETH.

## Impact Explanation
**Critical — Direct theft of user funds at rest.** User A's deposited altAgETH is transferred to User B with no recourse. This matches the allowed impact class "Direct theft of any user funds, whether at-rest or in-motion, other than unclaimed yield." The loss is immediate and total for any depositor whose altAgETH backs an unbacked bridge-minted supply.

## Likelihood Explanation
Granting `MINTER_ROLE` to a bridge is the explicitly documented and intended deployment pattern (contract NatDoc: "L2 chains for a canonical agETH token from Kelp"). No malicious or compromised bridge is required — the bridge legitimately mints tokens as part of normal cross-chain operation. The window between `mint()` and `depositBridgerAssets()` is structurally always present and exploitable by any recipient of bridge-minted tokens, including ordinary bridge users, without any special privileges.

## Recommendation
1. **Enforce a backing invariant in `_withdraw()`**: Before transferring, assert `ERC20Upgradeable(_asset).balanceOf(address(this)) - _amount >= totalSupply() - _amount` (i.e., the contract always remains fully collateralised after withdrawal).
2. **Separate accounting**: Track `depositBacked` supply independently from `mintBacked` supply; only allow `_withdraw()` to redeem against `depositBacked` tokens.
3. **Atomic backing**: Require `depositBridgerAssets()` to be called in the same transaction as `mint()`, or require the bridger to pre-fund the contract before minting.
4. **Restrict bridge-minted token redemption**: Bridge-minted tokens should only be burnable back through the bridge, not redeemable for altAgETH from the lockbox.

## Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.27;

// Foundry test — local fork or unit test
function test_bridgeMintDrainsDepositorFunds() public {
    // Setup: grant MINTER_ROLE to bridge (intended deployment)
    wrapper.grantRole(MINTER_ROLE, bridge);

    // Step 1: User A deposits 100e18 altAgETH
    vm.startPrank(userA);
    altAgETH.approve(address(wrapper), 100e18);
    wrapper.deposit(address(altAgETH), 100e18);
    vm.stopPrank();

    assertEq(altAgETH.balanceOf(address(wrapper)), 100e18);
    assertEq(wrapper.totalSupply(), 100e18);

    // Step 2: Bridge mints 100e18 wrapper tokens to userB (no altAgETH deposited)
    vm.prank(bridge);
    wrapper.mint(userB, 100e18);

    assertEq(altAgETH.balanceOf(address(wrapper)), 100e18); // unchanged
    assertEq(wrapper.totalSupply(), 200e18);                // doubled

    // Step 3: userB (unprivileged) withdraws altAgETH using unbacked tokens
    vm.prank(userB);
    wrapper.withdraw(address(altAgETH), 100e18);

    // userB stole userA's deposit
    assertEq(altAgETH.balanceOf(userB), 100e18);
    assertEq(altAgETH.balanceOf(address(wrapper)), 0);

    // userA's tokens are now permanently unbacked
    assertEq(wrapper.balanceOf(userA), 100e18);
    // withdraw by userA will revert (insufficient altAgETH in contract)
}
```