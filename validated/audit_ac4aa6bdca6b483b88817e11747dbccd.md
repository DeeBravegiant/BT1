Audit Report

## Title
Blocked Spender Bypasses Transfer Block via `transferFrom` — (File: `contracts/RSETH.sol`)

## Summary
`RSETH._transfer` enforces the block on `from` and `to` but never on `msg.sender` (the spender). Because `transferFrom` is not overridden, a blocked address that holds a pre-existing ERC-20 allowance can call `transferFrom(victim, unblocked_dest, amount)` and successfully drain the victim's rsETH — the block mechanism that was supposed to freeze the spender's ability to move funds is entirely bypassed.

## Finding Description
`RSETH` overrides `_transfer` to call `_enforceNotBlocked` on both endpoints:

```solidity
// contracts/RSETH.sol L287-291
function _transfer(address from, address to, uint256 amount) internal override {
    _enforceNotBlocked(from);
    _enforceNotBlocked(to);
    super._transfer(from, to, amount);
}
```

`transferFrom` is **not** overridden. The inherited `ERC20Upgradeable.transferFrom` path is:

```solidity
// lib/openzeppelin-contracts-upgradeable/.../ERC20Upgradeable.sol L163-168
function transferFrom(address from, address to, uint256 amount) public virtual override returns (bool) {
    address spender = _msgSender();
    _spendAllowance(from, spender, amount);  // spender = blocked router — never checked
    _transfer(from, to, amount);             // only checks from & to
    return true;
}
```

`msg.sender` (the spender) is never passed to `_enforceNotBlocked`. Exploit path:

1. User grants allowance: `rsETH.approve(router, type(uint256).max)`
2. Manager blocks the router: `rsETH.blockUserTransfers([router])` — `transfersBlockedUntil[router] > block.timestamp`
3. Blocked router calls `rsETH.transferFrom(user, attacker, rsETH.balanceOf(user))`
   - `_enforceNotBlocked(user)` → user not blocked → passes
   - `_enforceNotBlocked(attacker)` → attacker not blocked → passes
   - Transfer executes; user's rsETH is drained to attacker

The `recoverFrozenFunds` admin function only recovers tokens held *by* the blocked address, not tokens the blocked address has an allowance to spend — it does not mitigate this path.

## Impact Explanation
**Critical — Direct theft of user rsETH funds.** Any rsETH holder who has granted an allowance to a subsequently-blocked address can have their entire approved balance drained to an arbitrary unblocked destination. The block mechanism — the protocol's primary on-chain response to a compromised or sanctioned spender — is rendered ineffective for the spender role.

## Likelihood Explanation
**Medium.** Two realistic preconditions must coincide: (1) a user has an active `approve()` to a spender (routine for DeFi interactions — max-approvals to pools and routers are standard), and (2) the manager later blocks that spender (the exact use case `blockUserTransfers` is designed for: OFAC sanctions, compromised contracts). The attack window is the interval between the manager blocking the spender and the user revoking the approval. The blocked spender needs only call one public function (`transferFrom`) with no additional privileges.

## Recommendation
Override `transferFrom` in `RSETH.sol` to enforce the block on `msg.sender` before delegating to the parent:

```solidity
function transferFrom(address from, address to, uint256 amount)
    public
    virtual
    override
    returns (bool)
{
    _enforceNotBlocked(_msgSender()); // block the spender
    return super.transferFrom(from, to, amount);
}
```

Alternatively, override `_spendAllowance` to call `_enforceNotBlocked(_msgSender())`, which covers the same path without duplicating the `super` call.

## Proof of Concept

```solidity
address router   = makeAddr("router");
address attacker = makeAddr("attacker");

// 1. User approves router (normal DeFi interaction)
vm.prank(user);
rsETH.approve(router, type(uint256).max);

// 2. Manager blocks the router (e.g., OFAC action)
address[] memory accounts = new address[](1);
accounts[0] = router;
vm.prank(manager);
rsETH.blockUserTransfers(accounts);
assertTrue(rsETH.transfersBlockedUntil(router) > block.timestamp);

// 3. Blocked router drains user funds — _transfer checks user (ok) and attacker (ok), passes
vm.prank(router);
rsETH.transferFrom(user, attacker, rsETH.balanceOf(user));

// 4. Theft confirmed
assertEq(rsETH.balanceOf(user), 0);
assertGt(rsETH.balanceOf(attacker), 0);
```

The `_transfer` override at [1](#0-0)  only checks `from` and `to`, never `msg.sender`. The inherited `transferFrom` at [2](#0-1)  passes `_msgSender()` only to `_spendAllowance`, not to any block-enforcement hook. The `blockUserTransfers` function at [3](#0-2)  sets `transfersBlockedUntil[spender]` but this mapping is only consulted inside `_enforceNotBlocked`, which is never called for the spender in the `transferFrom` path. [4](#0-3)

### Citations

**File:** contracts/RSETH.sol (L161-177)
```text
    function blockUserTransfers(address[] calldata accounts) external onlyLRTManager {
        uint256 blockedUntil = block.timestamp + 1 days;
        uint256 length = accounts.length;

        for (uint256 i = 0; i < length; ++i) {
            address account = accounts[i];

            if (isPermanentlyExempt[account] || account == address(0)) continue;

            uint256 prevBlockedUntil = transfersBlockedUntil[account];

            if (blockedUntil != prevBlockedUntil) {
                transfersBlockedUntil[account] = blockedUntil;
                emit UserTransfersBlocked(account, blockedUntil);
            }
        }
    }
```

**File:** contracts/RSETH.sol (L287-291)
```text
    function _transfer(address from, address to, uint256 amount) internal override {
        _enforceNotBlocked(from);
        _enforceNotBlocked(to);
        super._transfer(from, to, amount);
    }
```

**File:** contracts/RSETH.sol (L293-306)
```text
    /// @dev Reverts if `account` is currently blocked (used for transfers, mints, and burns)
    function _enforceNotBlocked(address account) internal {
        // Addresses that are permanently exempt can never be blocked
        if (isPermanentlyExempt[account]) return;

        // Check if the account has an active transfer block
        uint256 blockedUntil = transfersBlockedUntil[account];
        if (blockedUntil == 0) return;

        if (block.timestamp < blockedUntil) revert TransfersBlocked(account, blockedUntil);

        // Auto-clean up expired block
        delete transfersBlockedUntil[account];
    }
```

**File:** lib/openzeppelin-contracts-upgradeable/contracts/token/ERC20/ERC20Upgradeable.sol (L163-168)
```text
    function transferFrom(address from, address to, uint256 amount) public virtual override returns (bool) {
        address spender = _msgSender();
        _spendAllowance(from, spender, amount);
        _transfer(from, to, amount);
        return true;
    }
```
