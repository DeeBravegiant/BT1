### Title
`BURNER_ROLE` Can Burn Any Amount of rsETH From an Arbitrary Address Without Allowance - (File: contracts/RSETH.sol)

### Summary
`RSETH.burnFrom` allows any address holding `BURNER_ROLE` to destroy an arbitrary amount of rsETH from any user's account without requiring an ERC-20 allowance from that user. This is the direct analog of the reported Yieldy vulnerability: a privileged burn role can unilaterally destroy any holder's tokens.

### Finding Description
In `contracts/RSETH.sol`, the `burnFrom` function is:

```solidity
function burnFrom(address account, uint256 amount) external onlyRole(LRTConstants.BURNER_ROLE) whenNotPaused {
    _enforceNotBlocked(account);
    _burn(account, amount);
}
```

It calls `_burn(account, amount)` directly — bypassing the standard ERC-20 allowance mechanism entirely. There is no `_spendAllowance` call, no check that `account` has approved the caller, and no cap on the amount. Any address granted `BURNER_ROLE` can burn the full balance of any rsETH holder at will.

This contrasts with `contracts/ccip/WrappedRSETH.sol`, where `burnFrom` correctly delegates to `super.burnFrom` (OZ `ERC20Burnable`), which enforces allowance via `_spendAllowance` before calling `_burn`. The vulnerability is isolated to `RSETH.sol`. [1](#0-0) [2](#0-1) 

### Impact Explanation
rsETH is the protocol's liquid restaking token. Burning a user's rsETH destroys their on-chain claim to the underlying staked ETH held in the protocol. A malicious or compromised `BURNER_ROLE` address can:

1. Call `burnFrom(victim, victim_balance)` for any holder.
2. Reduce the victim's rsETH balance to zero.
3. The victim loses their proportional claim to the underlying ETH — a direct, permanent loss of funds.

Impact: **Critical** — direct theft/permanent destruction of any user's funds at rest. [3](#0-2) 

### Likelihood Explanation
`BURNER_ROLE` is a protocol-level operator role managed through `LRTConfig`. The role is intended for legitimate protocol operations (e.g., burning rsETH during withdrawals via `LRTWithdrawalManager`). However, the function grants far more power than needed: it can target any address, not just addresses that have approved the caller. A single compromised or malicious `BURNER_ROLE` key is sufficient to drain every rsETH holder simultaneously. The unnecessary breadth of the permission elevates the risk of any key compromise into a protocol-wide catastrophe. [3](#0-2) 

### Recommendation
Replace the direct `_burn` call with the standard allowance-enforcing path, or restrict burning to only `msg.sender`:

```solidity
// Option A: only allow self-burn
function burn(uint256 amount) external whenNotPaused {
    _enforceNotBlocked(msg.sender);
    _burn(msg.sender, amount);
}

// Option B: keep role-based burn but enforce allowance
function burnFrom(address account, uint256 amount) external onlyRole(LRTConstants.BURNER_ROLE) whenNotPaused {
    _enforceNotBlocked(account);
    _spendAllowance(account, msg.sender, amount);
    _burn(account, amount);
}
```

If the protocol genuinely requires the `BURNER_ROLE` to burn without allowance (e.g., for withdrawal processing), scope the function to only burn from addresses that have an active withdrawal request, not from arbitrary addresses.

### Proof of Concept
1. Alice holds 100 rsETH (`RSETH.balanceOf(alice) == 100e18`).
2. Attacker controls an address with `BURNER_ROLE` (e.g., a compromised withdrawal manager key).
3. Attacker calls `RSETH.burnFrom(alice, 100e18)`.
4. No allowance check occurs; `_burn(alice, 100e18)` executes.
5. Alice's rsETH balance is zero. Her claim to the underlying staked ETH is permanently destroyed.
6. Alice has no recourse — the burn is irreversible. [3](#0-2)

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
