Audit Report

## Title
`burnFrom` Delegates to `ERC20Burnable.burnFrom`, Requiring Allowance That CCIP Pool Never Grants — (`contracts/ccip/WrappedRSETH.sol`)

## Summary

`WrappedRSETH.burnFrom` calls `super.burnFrom(account, amount)`, which unconditionally invokes `_spendAllowance(account, _msgSender(), amount)` before burning. The CCIP `BurnMintTokenPool` calls `burn(address(this), amount)` — the two-argument form — which routes through `burnFrom`, triggering an allowance check that the pool never satisfies. Every bridge-back (L2→L1) reverts, permanently freezing all WrappedRSETH held on L2.

## Finding Description

`burn(address, uint256)` is a thin alias that unconditionally delegates to `burnFrom`: [1](#0-0) 

`burnFrom` passes the `onlyBurner` guard but then calls `super.burnFrom`, which is `ERC20Burnable.burnFrom`: [2](#0-1) 

`ERC20Burnable.burnFrom` unconditionally calls `_spendAllowance(account, _msgSender(), amount)` before burning: [3](#0-2) 

When the CCIP `BurnMintTokenPool` calls `burn(address(this), amount)` (pool burning its own balance), the call chain becomes `burnFrom(pool, pool)` → `_spendAllowance(pool, pool, amount)` → checks `allowance[pool][pool]` → 0 → revert. The pool is a registered burner so `onlyBurner` passes, but the allowance check is an independent, unconditional gate that the pool never satisfies because CCIP pools do not self-approve. The `_approve` override's `validAddress` guard only blocks approvals to `address(this)` (the token contract itself), not pool self-approvals, but the pool simply never issues one. [4](#0-3) 

The contract's own NatDoc references `BurnMintERC677` as the intended model; that reference implementation calls `_burn(account, amount)` directly in `burn(address, uint256)`, bypassing `_spendAllowance` entirely. `WrappedRSETH` diverges from this model by routing through `super.burnFrom`. [5](#0-4) 

## Impact Explanation

Every bridge-back operation fails at the burn step. Users holding WrappedRSETH on L2 cannot redeem it for rsETH on L1 through the CCIP bridge. This freezes both principal and accrued yield — not merely unclaimed yield. The correct classification is **Critical — Permanent freezing of funds**, since the entire token balance (principal + yield) is irrecoverable via the bridge. The submitted claim understates the impact as Medium (unclaimed yield only); the evidence proves the higher impact because the principal itself is also frozen.

## Likelihood Explanation

The revert fires on every bridge-back call through the standard CCIP pool interface. No special attacker action is required; any ordinary user attempting to bridge WrappedRSETH from L2 to L1 triggers it. The condition is deterministic and repeatable.

## Recommendation

Replace `super.burnFrom(account, amount)` with a direct `_burn(account, amount)` call inside `WrappedRSETH.burnFrom`, consistent with the referenced `BurnMintERC677`:

```solidity
function burnFrom(address account, uint256 amount)
    public override(IBurnMintERC20, ERC20Burnable) onlyBurner {
    _burn(account, amount); // role guard is sufficient; no allowance check needed
}
```

The `onlyBurner` modifier already enforces access control. The ERC20 allowance check is redundant and incompatible with the CCIP burn-mint pool pattern.

## Proof of Concept

```solidity
function test_burnFrom_burnerNoAllowance_reverts() public {
    address pool = address(0xBEEF);
    address user = address(0xCAFE);
    uint256 amount = 1e18;

    vm.prank(owner);
    wrappedRSETH.grantBurnRole(pool);
    vm.prank(owner);
    wrappedRSETH.grantMintRole(owner);
    vm.prank(owner);
    wrappedRSETH.mint(pool, amount); // pool holds tokens (as in CCIP lockOrBurn)

    // Pool has burner role but no self-approval
    assertEq(wrappedRSETH.allowance(pool, pool), 0);

    // Simulate CCIP pool calling burn(address(this), amount)
    vm.prank(pool);
    vm.expectRevert("ERC20: insufficient allowance");
    wrappedRSETH.burn(pool, amount); // routes: burn(addr,amt) → burnFrom → _spendAllowance → revert
}
```

### Citations

**File:** contracts/ccip/WrappedRSETH.sol (L16-18)
```text
/// @notice An audited ERC677 compatible token contract with burn and minting roles.
/// @dev reference:
/// https://github.com/smartcontractkit/ccip/blob/ccip-develop/contracts/src/v0.8/shared/token/ERC677/BurnMintERC677.sol
```

**File:** contracts/ccip/WrappedRSETH.sol (L84-86)
```text
    function _approve(address owner, address spender, uint256 amount) internal virtual override validAddress(spender) {
        super._approve(owner, spender, amount);
    }
```

**File:** contracts/ccip/WrappedRSETH.sol (L122-124)
```text
    function burn(address account, uint256 amount) public virtual override {
        burnFrom(account, amount);
    }
```

**File:** contracts/ccip/WrappedRSETH.sol (L129-131)
```text
    function burnFrom(address account, uint256 amount) public override(IBurnMintERC20, ERC20Burnable) onlyBurner {
        super.burnFrom(account, amount);
    }
```

**File:** lib/openzeppelin-contracts/contracts/token/ERC20/extensions/ERC20Burnable.sol (L35-38)
```text
    function burnFrom(address account, uint256 amount) public virtual {
        _spendAllowance(account, _msgSender(), amount);
        _burn(account, amount);
    }
```
