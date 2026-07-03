The code confirms the claim. Let me verify the inheritance and OpenZeppelin hook behavior.

Audit Report

## Title
`RSETH._transfer` Override Omits `whenNotPaused` Guard, Allowing rsETH Transfers to Bypass the Emergency Pause — (File: `contracts/RSETH.sol`)

## Summary
`RSETH` inherits `ERC20Upgradeable` and `PausableUpgradeable` separately, providing no automatic pause hook on the ERC-20 transfer path. The `_transfer` override at lines 287–291 enforces the per-address block list but omits `whenNotPaused`, while `mint` (line 235) and `burnFrom` (line 245) both correctly gate on the pause state. Any rsETH holder can therefore call `transfer` or `transferFrom` freely while the contract is paused, defeating the emergency stop for user-initiated token movements.

## Finding Description
`RSETH` is declared as:

```solidity
// contracts/RSETH.sol L13
contract RSETH is Initializable, LRTConfigRoleChecker, ERC20Upgradeable, PausableUpgradeable {
```

It does **not** inherit `ERC20PausableUpgradeable`, so no `_beforeTokenTransfer` hook wires the pause state into the ERC-20 transfer path. The only transfer-path override is:

```solidity
// contracts/RSETH.sol L287-291
function _transfer(address from, address to, uint256 amount) internal override {
    _enforceNotBlocked(from);
    _enforceNotBlocked(to);
    super._transfer(from, to, amount);   // no whenNotPaused
}
```

Both privileged mutation functions correctly include the guard:

```solidity
// L235 — mint
whenNotPaused

// L245 — burnFrom
function burnFrom(...) external onlyRole(LRTConstants.BURNER_ROLE) whenNotPaused {
```

There is no `_beforeTokenTransfer` override anywhere in `RSETH.sol` that could compensate. The exploit path is: (1) contract is paused by a `PAUSER_ROLE` holder via `pause()`; (2) any rsETH holder calls `transfer(recipient, amount)`; (3) `_transfer` is invoked, checks only the block list, and calls `super._transfer` — the pause state is never consulted; (4) the transfer succeeds.

The most concrete harm is the `recoverFrozenFunds` race: an admin pauses to investigate a suspicious address, then calls `blockUserTransfers`. During the pause window — before the block lands — the target can transfer their entire balance to a fresh address. `recoverFrozenFunds` then recovers zero from the original address, and the fresh address is unblocked.

## Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**

The pause mechanism's documented purpose is to halt all token activity during an incident. For `mint` and `burnFrom` this holds; for `transfer`/`transferFrom` it does not. No funds are directly stolen by the missing modifier alone, but the protocol's emergency-stop guarantee is not upheld for the transfer path, and the `recoverFrozenFunds` mechanism can be circumvented during any pause event.

## Likelihood Explanation
Any rsETH holder can trigger this with a standard `transfer` call — no special role, no oracle dependency, no flash loan. The precondition is simply that the contract is paused, a state that can persist for hours or days during incident response. The window is the entire duration of every pause event.

## Recommendation
Add `whenNotPaused` to the `_transfer` override:

```solidity
function _transfer(address from, address to, uint256 amount) internal override whenNotPaused {
    _enforceNotBlocked(from);
    _enforceNotBlocked(to);
    super._transfer(from, to, amount);
}
```

Alternatively, override `_beforeTokenTransfer` to call `require(!paused(), "Pausable: paused")`, which is the standard OpenZeppelin pattern used by `ERC20PausableUpgradeable`.

## Proof of Concept

```solidity
function testTransferBypassesPause() public {
    // Setup: mint rsETH to user
    vm.prank(minter);
    rsETH.mint(user, 1e18);

    // Admin pauses the contract
    vm.prank(pauser);
    rsETH.pause();
    assertTrue(rsETH.paused());

    // mint is correctly blocked
    vm.expectRevert("Pausable: paused");
    vm.prank(minter);
    rsETH.mint(user, 1e18);

    // burnFrom is correctly blocked
    vm.expectRevert("Pausable: paused");
    vm.prank(burner);
    rsETH.burnFrom(user, 1e18);

    // transfer is NOT blocked — succeeds while paused
    vm.prank(user);
    rsETH.transfer(user2, 1e18);           // should revert, but doesn't
    assertEq(rsETH.balanceOf(user2), 1e18); // funds moved during pause
}
```

The test confirms that `transfer` succeeds while `mint` and `burnFrom` correctly revert, demonstrating the incomplete pause enforcement in `RSETH._transfer` at lines 287–291.