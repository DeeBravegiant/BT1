Audit Report

## Title
`burn(address,uint256)` Silently Requires Allowance, Breaking CCIP Burn-and-Mint Pool Integration — (File: `contracts/ccip/WrappedRSETH.sol`)

## Summary
`WrappedRSETH.burn(address,uint256)` unconditionally delegates to `burnFrom`, which calls OZ `ERC20Burnable.burnFrom` and therefore invokes `_spendAllowance`. The Chainlink `BurnMintERC677` reference that `WrappedRSETH` explicitly models calls `_burn` directly with no allowance check. Any CCIP pool that calls `burn(account, amount)` without a prior `approve` from `account` to the pool will revert on every burn attempt, permanently freezing bridged funds.

## Finding Description
`WrappedRSETH` declares itself as implementing `IBurnMintERC20` and cites `BurnMintERC677` as its reference implementation. [1](#0-0) 

The two-argument `burn(address,uint256)` is implemented as a pure alias to `burnFrom`: [2](#0-1) 

`burnFrom` passes the `onlyBurner` guard and then calls `super.burnFrom`: [3](#0-2) 

`super.burnFrom` is OZ `ERC20Burnable.burnFrom`, which unconditionally calls `_spendAllowance(account, _msgSender(), amount)` before `_burn`: [4](#0-3) 

The `IBurnMintERC20` interface carries no allowance requirement in its NatSpec for `burn(address,uint256)`: [5](#0-4) 

Full call chain for a CCIP pool calling `burn(user, amount)`:
```
pool.lockOrBurn(user, amount)
  → WrappedRSETH.burn(user, amount)           // L122
    → WrappedRSETH.burnFrom(user, amount)     // L123
      → ERC20Burnable.burnFrom(user, amount)  // L130 super call
        → _spendAllowance(user, pool, amount) // REVERTS — no allowance set
```

The Chainlink reference `BurnMintERC677.burn(address,uint256)` calls `_burn(account, amount)` directly, with no `_spendAllowance`. The deviation is the root cause.

## Impact Explanation
**Critical — Permanent freezing of funds.** A CCIP `BurnMintTokenPool` or `BurnFromMintTokenPool` that calls `burn(account, amount)` will revert on every burn attempt. Tokens minted on the destination chain cannot be burned when bridging back; the corresponding locked tokens on the source chain can never be released. This constitutes permanent freezing of user funds in the CCIP bridge with no recovery path absent a contract upgrade.

## Likelihood Explanation
`WrappedRSETH` is purpose-built for CCIP (it lives in `contracts/ccip/`, implements `IBurnMintERC20`, and explicitly cites `BurnMintERC677` as its model). The Chainlink CCIP pool pattern calls `burn(originalSender, amount)` directly on the token contract. The CCIP pool holds the burner role by design — no privilege escalation is required. The mismatch is triggered on every cross-chain transfer that routes through the burn path, with no special attacker action needed beyond initiating a normal bridge transfer.

## Recommendation
Override `burn(address,uint256)` to call `_burn` directly, matching the Chainlink reference, while keeping the `onlyBurner` guard:

```solidity
function burn(address account, uint256 amount)
    public virtual override onlyBurner
{
    _burn(account, amount);
}
```

`burnFrom` should remain unchanged for callers that explicitly require allowance-based burning.

## Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

import "forge-std/Test.sol";
import "../contracts/ccip/WrappedRSETH.sol";

contract BurnMismatchTest is Test {
    WrappedRSETH token;
    address owner = address(0xA);
    address pool  = address(0xB); // simulated CCIP burner
    address user  = address(0xC);

    function setUp() public {
        vm.prank(owner);
        token = new WrappedRSETH("Wrapped rsETH", "wrsETH", 18, 0, owner);
        vm.prank(owner);
        token.grantBurnRole(pool);
        vm.prank(owner);
        token.grantMintRole(owner);
        vm.prank(owner);
        token.mint(user, 1 ether);
    }

    // Demonstrates burn(address,uint256) reverts without allowance
    function test_burnRevertsWithoutAllowance() public {
        vm.prank(pool);
        vm.expectRevert("ERC20: insufficient allowance");
        token.burn(user, 1 ether); // standard CCIP pool call — always reverts
    }
}
```

Running `test_burnRevertsWithoutAllowance` confirms the revert. The pool holds the burner role and calls `burn(user, amount)` exactly as a CCIP pool would — no allowance is ever set by the CCIP protocol flow, so every burn attempt fails.

### Citations

**File:** contracts/ccip/WrappedRSETH.sol (L16-18)
```text
/// @notice An audited ERC677 compatible token contract with burn and minting roles.
/// @dev reference:
/// https://github.com/smartcontractkit/ccip/blob/ccip-develop/contracts/src/v0.8/shared/token/ERC677/BurnMintERC677.sol
```

**File:** contracts/ccip/WrappedRSETH.sol (L119-124)
```text
    /// @inheritdoc IBurnMintERC20
    /// @dev Alias for BurnFrom for compatibility with the older naming convention.
    /// @dev Uses burnFrom for all validation & logic.
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

**File:** contracts/ccip/IBurnMintERC20.sol (L18-22)
```text
    /// @notice Burns tokens from a given address..
    /// @param account The address to burn tokens from.
    /// @param amount The number of tokens to be burned.
    /// @dev this function decreases the total supply.
    function burn(address account, uint256 amount) external;
```
