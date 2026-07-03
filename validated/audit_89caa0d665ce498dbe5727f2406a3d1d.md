### Title
`BURNER_ROLE` Can Burn Any Amount of rsETH from an Arbitrary Address Without Allowance - (File: contracts/RSETH.sol)

### Summary
`RSETH.burnFrom()` allows any address holding `BURNER_ROLE` to burn an arbitrary amount of rsETH from **any** address without requiring an ERC-20 allowance from the token holder. This is a direct analog of the reported L2LivepeerToken vulnerability: a malicious or compromised `BURNER_ROLE` can drain any rsETH holder — including AMM liquidity pools — causing direct theft of user funds.

### Finding Description
In `contracts/RSETH.sol`, the `burnFrom` function is:

```solidity
function burnFrom(address account, uint256 amount) external onlyRole(LRTConstants.BURNER_ROLE) whenNotPaused {
    _enforceNotBlocked(account);
    _burn(account, amount);
}
```

The function accepts an arbitrary `account` parameter and calls `_burn(account, amount)` directly — bypassing any ERC-20 allowance check. There is no `_spendAllowance(account, msg.sender, amount)` call, no `require(account == msg.sender)` guard, and no other constraint on which address can be targeted. The only gate is the `BURNER_ROLE` role check on the caller.

This is structurally identical to the reported L2LivepeerToken bug:

```solidity
// L2LivepeerToken (vulnerable)
function burn(address _from, uint256 _amount) external onlyRole(BURNER_ROLE) {
    _burn(_from, _amount);
}

// RSETH (vulnerable)
function burnFrom(address account, uint256 amount) external onlyRole(LRTConstants.BURNER_ROLE) whenNotPaused {
    _burn(account, amount);
}
```

By contrast, `WrappedRSETH.burnFrom` (the CCIP wrapper) correctly delegates to `super.burnFrom`, which is `ERC20Burnable.burnFrom` and does enforce `_spendAllowance`. `RSETH.burnFrom` does not inherit from `ERC20Burnable` and has no such protection.

### Impact Explanation
**Critical — Direct theft of any user funds.**

A malicious or compromised `BURNER_ROLE` address can call `burnFrom(victim, victimBalance)` for any address. Concrete high-value targets include:

- An rsETH/WETH Uniswap v3 pool: burning the pool's entire rsETH balance collapses the pool's rsETH reserve to zero, allowing the attacker to drain all WETH from the pool at near-zero cost via a swap.
- Any individual rsETH holder's wallet.

The token destruction is irreversible; there is no recovery path for burned tokens.

### Likelihood Explanation
**Medium.** Exploitation requires the `BURNER_ROLE` key to be malicious or compromised. However:

- The `BURNER_ROLE` is a hot operational key (used on every withdrawal to burn rsETH), making it a high-value target for key compromise.
- The design itself is unnecessarily dangerous: the role has no need to burn from arbitrary addresses; it only needs to burn from the caller (or from an address that has granted allowance).
- The identical design flaw was confirmed and fixed in the Livepeer bridge, establishing that this pattern is recognized as a real risk.

### Recommendation
Remove the `account` parameter from `burnFrom` and restrict burning to `msg.sender` only:

```solidity
function burn(uint256 amount) external whenNotPaused {
    _enforceNotBlocked(msg.sender);
    _burn(msg.sender, amount);
}
```

If the protocol requires a privileged role to burn on behalf of another address (e.g., during withdrawal processing), the caller should first receive an ERC-20 allowance from the account and use `_spendAllowance` before calling `_burn`, mirroring the standard `ERC20Burnable.burnFrom` pattern.

### Proof of Concept

1. `BURNER_ROLE` is granted to `BurnerAddr` (e.g., the withdrawal manager contract).
2. `BurnerAddr` key is compromised, or the role is granted to a malicious contract.
3. Attacker identifies the Uniswap rsETH/WETH pool address (`poolAddr`) holding, say, 10,000 rsETH.
4. Attacker calls: `RSETH.burnFrom(poolAddr, 10_000e18)`.
5. The call passes `onlyRole(BURNER_ROLE)` and `_enforceNotBlocked(poolAddr)` (pool is not blocked), then executes `_burn(poolAddr, 10_000e18)`.
6. The pool's rsETH balance drops to zero while its WETH reserve is unchanged.
7. Attacker swaps a negligible amount of rsETH into the pool, receiving nearly all WETH at the now-collapsed price.
8. All WETH liquidity is stolen; LP providers suffer total loss. [1](#0-0) [2](#0-1)

### Citations

**File:** contracts/RSETH.sol (L242-248)
```text
    /// @notice Burns rsETH when called by an authorized caller
    /// @param account the account to burn from
    /// @param amount the amount of rsETH to burn
    function burnFrom(address account, uint256 amount) external onlyRole(LRTConstants.BURNER_ROLE) whenNotPaused {
        _enforceNotBlocked(account);
        _burn(account, amount);
    }
```

**File:** contracts/ccip/WrappedRSETH.sol (L126-131)
```text
    /// @inheritdoc ERC20Burnable
    /// @dev Uses OZ ERC20 _burn to disallow burning from address(0).
    /// @dev Decreases the total supply.
    function burnFrom(address account, uint256 amount) public override(IBurnMintERC20, ERC20Burnable) onlyBurner {
        super.burnFrom(account, amount);
    }
```
