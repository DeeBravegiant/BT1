### Title
rsETH Transfers Bypass Pause — Missing `whenNotPaused` in `RSETH._transfer` (File: contracts/RSETH.sol)

---

### Summary

The `RSETH` contract's `_transfer` override is missing the `whenNotPaused` modifier. While `mint` and `burnFrom` are correctly gated by `whenNotPaused`, the internal `_transfer` hook — which underlies every ERC20 `transfer` and `transferFrom` call — is not. Any rsETH holder can freely transfer tokens even when the contract is paused, defeating the purpose of the pause mechanism.

---

### Finding Description

`RSETH` inherits `PausableUpgradeable` and consistently applies `whenNotPaused` to state-changing mint and burn paths:

```solidity
// contracts/RSETH.sol line 230
function mint(address to, uint256 amount)
    external
    onlyRole(LRTConstants.MINTER_ROLE)
    whenNotPaused          // ← guarded
    checkDailyMintLimit(amount)
{ ... }

// contracts/RSETH.sol line 245
function burnFrom(address account, uint256 amount)
    external
    onlyRole(LRTConstants.BURNER_ROLE)
    whenNotPaused          // ← guarded
{ ... }
``` [1](#0-0) 

However, the `_transfer` override that enforces the per-address block mechanism does **not** include `whenNotPaused`:

```solidity
// contracts/RSETH.sol line 287
function _transfer(address from, address to, uint256 amount) internal override {
    _enforceNotBlocked(from);
    _enforceNotBlocked(to);
    super._transfer(from, to, amount);   // no whenNotPaused check
}
``` [2](#0-1) 

Because `ERC20Upgradeable.transfer` and `transferFrom` both route through `_transfer`, any rsETH holder can invoke them successfully while the contract is paused. The pause only blocks minting and burning; it does not freeze token movement.

---

### Impact Explanation

When the RSETH contract is paused in response to a security incident (e.g., a slashing event, oracle manipulation, or protocol exploit), the protocol's ability to contain damage is undermined. rsETH holders can:

- Transfer tokens to new wallets, evading any subsequent per-address freeze applied via `blockUserTransfers`.
- Continue trading rsETH on DEXes, spreading exposure during an active incident.
- Move tokens before any admin-initiated `recoverFrozenFunds` action can target the original address.

The contract fails to deliver its promised invariant — that pausing halts all rsETH token operations — without causing direct fund loss. This maps to **Low: Contract fails to deliver promised returns, but doesn't lose value.**

---

### Likelihood Explanation

The `PAUSER_ROLE` can pause RSETH at any time. The pause is a realistic operational action taken during incidents. Once paused, the bypass is immediately exploitable by any rsETH holder with no additional preconditions, no privileged access, and no special knowledge — a standard ERC20 `transfer` call suffices.

---

### Recommendation

Add `whenNotPaused` to the `_transfer` override so that all token movement is halted consistently with `mint` and `burnFrom` when the contract is paused:

```solidity
function _transfer(address from, address to, uint256 amount)
    internal
    override
    whenNotPaused          // add this
{
    _enforceNotBlocked(from);
    _enforceNotBlocked(to);
    super._transfer(from, to, amount);
}
``` [2](#0-1) 

---

### Proof of Concept

1. RSETH contract is paused by an address holding `PAUSER_ROLE`.
2. Attacker (any rsETH holder) calls `RSETH.transfer(newWallet, balance)`.
3. `ERC20Upgradeable.transfer` calls `_transfer(msg.sender, newWallet, balance)`.
4. `_transfer` executes `_enforceNotBlocked` (passes — address is not blocked) then `super._transfer` — **no pause check is performed**.
5. Transfer succeeds. rsETH moves freely despite the contract being in a paused state, bypassing the intended operational freeze. [3](#0-2) [2](#0-1)

### Citations

**File:** contracts/RSETH.sol (L183-191)
```text
    /// @dev Triggers stopped state. Contract must not be paused.
    function pause() external onlyRole(LRTConstants.PAUSER_ROLE) {
        _pause();
    }

    /// @dev Returns to normal state. Contract must be paused.
    function unpause() external onlyLRTAdmin {
        _unpause();
    }
```

**File:** contracts/RSETH.sol (L229-248)
```text
    function mint(
        address to,
        uint256 amount
    )
        external
        onlyRole(LRTConstants.MINTER_ROLE)
        whenNotPaused
        checkDailyMintLimit(amount)
    {
        _enforceNotBlocked(to);
        _mint(to, amount);
    }

    /// @notice Burns rsETH when called by an authorized caller
    /// @param account the account to burn from
    /// @param amount the amount of rsETH to burn
    function burnFrom(address account, uint256 amount) external onlyRole(LRTConstants.BURNER_ROLE) whenNotPaused {
        _enforceNotBlocked(account);
        _burn(account, amount);
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
