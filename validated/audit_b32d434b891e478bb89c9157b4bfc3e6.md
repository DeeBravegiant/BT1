### Title
rsETH Transfer Restriction (`_enforceNotBlocked`) Causes Unexpected Reverts in 3rd-Party Contracts Integrating rsETH - (File: contracts/RSETH.sol)

---

### Summary
The `RSETH` token overrides the standard ERC20 `_transfer` function to enforce per-address transfer blocks on both the `from` and `to` sides of every transfer. This non-standard behavior breaks ERC20 invariants and can cause unexpected, silent reverts in any 3rd-party contract (DEX, lending market, yield aggregator) that integrates rsETH, temporarily freezing user funds held in those protocols.

---

### Finding Description
`RSETH.sol` overrides `_transfer` as follows:

```solidity
function _transfer(address from, address to, uint256 amount) internal override {
    _enforceNotBlocked(from);
    _enforceNotBlocked(to);
    super._transfer(from, to, amount);
}
```

`_enforceNotBlocked` reverts with `TransfersBlocked(account, blockedUntil)` if the address has an active block set by `blockUserTransfers`:

```solidity
function _enforceNotBlocked(address account) internal {
    if (isPermanentlyExempt[account]) return;
    uint256 blockedUntil = transfersBlockedUntil[account];
    if (blockedUntil == 0) return;
    if (block.timestamp < blockedUntil) revert TransfersBlocked(account, blockedUntil);
    delete transfersBlockedUntil[account];
}
```

The same guard is applied in `mint` (checking `to`) and `burnFrom` (checking `account`):

```solidity
function mint(address to, uint256 amount) external ... {
    _enforceNotBlocked(to);
    _mint(to, amount);
}

function burnFrom(address account, uint256 amount) external ... {
    _enforceNotBlocked(account);
    _burn(account, amount);
}
```

The manager can block any non-exempt address for 24 hours:

```solidity
function blockUserTransfers(address[] calldata accounts) external onlyLRTManager {
    uint256 blockedUntil = block.timestamp + 1 days;
    ...
    transfersBlockedUntil[account] = blockedUntil;
}
```

Any 3rd-party contract (DEX, lending protocol, yield vault) that holds rsETH on behalf of users, or that routes rsETH transfers as part of a multi-step transaction, will revert if either the `from` or `to` address in the transfer is blocked. The 3rd-party contract has no way to anticipate or handle this non-standard revert, because standard ERC20 does not define such a restriction.

---

### Impact Explanation
**Temporary freezing of funds (Medium).**

When a user's address is blocked:
1. Any 3rd-party contract that attempts to transfer rsETH to or from that user reverts. The user's rsETH deposited in DEX pools, lending markets, or yield aggregators becomes inaccessible for up to 24 hours.
2. The user cannot call `instantWithdrawal` in `LRTWithdrawalManager`, because `burnFrom(msg.sender, ...)` will revert on the blocked address.
3. The user cannot initiate a new withdrawal via `initiateWithdrawal`, because the `safeTransferFrom(msg.sender, address(this), rsETHUnstaked)` call will revert.
4. A 3rd-party contract that is itself blocked (e.g., a DEX pool address blocked by the manager) will cause all users of that pool to be unable to move rsETH through it, freezing funds for all participants.

---

### Likelihood Explanation
The manager legitimately uses `blockUserTransfers` to freeze suspicious addresses. If a blocked user has rsETH deposited in any 3rd-party DeFi protocol, that protocol's attempt to transfer rsETH to or from the user will revert. This is a realistic operational scenario: rsETH is designed to be composable across DeFi, so users holding rsETH in external protocols is expected. The 24-hour block window is sufficient to cause meaningful disruption to time-sensitive operations (e.g., liquidations, arbitrage, yield harvesting).

---

### Recommendation
- Maintain a `isPermanentlyExempt` whitelist (already present) and proactively add known 3rd-party integration contracts (DEX pools, lending markets) to it before they accumulate user funds.
- Consider whether the `_enforceNotBlocked` check on the `to` address in `_transfer` is necessary; blocking only the `from` side would reduce the blast radius on 3rd-party recipients.
- Document clearly that rsETH is not a fully ERC20-compliant token so that integrators can implement appropriate fallback handling.

---

### Proof of Concept

1. Alice deposits rsETH into a DEX liquidity pool (e.g., Uniswap v3 or Curve). The pool contract holds rsETH on Alice's behalf.
2. The LRT manager calls `blockUserTransfers([alice])`, setting `transfersBlockedUntil[alice] = block.timestamp + 1 days`.
3. Alice calls the DEX pool's `withdraw` function, which internally calls `rsETH.transfer(alice, amount)`.
4. Inside `_transfer`, `_enforceNotBlocked(alice)` reverts with `TransfersBlocked(alice, blockedUntil)`.
5. The DEX pool's `withdraw` transaction reverts entirely. Alice's rsETH is inaccessible in the pool for up to 24 hours.
6. If the DEX pool is used by other users during this period, any operation that routes rsETH through Alice's position also reverts, blocking unrelated users.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** contracts/RSETH.sol (L229-240)
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
```

**File:** contracts/RSETH.sol (L245-248)
```text
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

**File:** contracts/RSETH.sol (L294-306)
```text
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
