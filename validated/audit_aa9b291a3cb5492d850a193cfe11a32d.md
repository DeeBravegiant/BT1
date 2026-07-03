### Title
Permanent Transfer-Exemption Status Cannot Be Revoked, Disabling Admin Freeze/Recovery for Exempt Addresses - (File: contracts/RSETH.sol)

### Summary
`RSETH.addPermanentExemptions` irrevocably marks addresses as exempt from transfer blocks. No function exists to remove the exemption. If a permanently exempt address later misbehaves or is compromised, the admin permanently loses the ability to block its transfers or recover its rsETH balance, mirroring M-15's pattern of an immutable state variable that strips admin enforcement power.

### Finding Description
`addPermanentExemptions` sets `isPermanentlyExempt[account] = true` and the NatSpec explicitly labels this "non-reversible". A grep across all production Solidity files confirms there is no function that ever writes `isPermanentlyExempt[account] = false` or deletes the mapping entry.

Three downstream enforcement paths are permanently disabled for any such address:

1. **`blockUserTransfers`** (line 168) silently `continue`s when `isPermanentlyExempt[account]` is true, so the manager can never impose a 24-hour transfer hold.
2. **`recoverFrozenFunds`** (line 210) hard-reverts with `AddressPermanentlyExempt`, so the admin can never seize the balance even in an emergency.
3. **`_enforceNotBlocked`** (line 296) returns early, so the ERC-20 `_transfer`, `mint`, and `burnFrom` hooks never enforce a block.

The parallel to M-15 is exact: just as a linked tophat's `eligibility` is permanently `address(0)` and the admin can never revoke a wearer's standing, a permanently exempt rsETH address has its exemption flag permanently `true` and the admin can never freeze or recover its tokens.

### Impact Explanation
Once an address is permanently exempted — whether a DEX pool, bridge contract, or any other address — the admin loses all freeze-and-recover capability over it forever. If that address is later exploited, upgraded maliciously, or added by mistake, the protocol's emergency response mechanism (block + recover) is completely inoperative for it. The contract fails to deliver its promised admin-enforcement guarantee for these addresses.

**Impact level: Low** — Contract fails to deliver promised returns (admin enforcement), but no direct value loss occurs from the exemption itself.

### Likelihood Explanation
Permanently exempt addresses are expected to be protocol-level contracts (pools, bridges, wrappers). Any one of them being upgraded to a malicious implementation, or a manager mistakenly exempting a wrong address, triggers the issue. The likelihood is low-to-medium but the consequence is irreversible.

### Recommendation
Add an admin-only function to revoke permanent exemptions:

```solidity
function removePermanentExemption(address account) external onlyLRTAdmin {
    if (!isPermanentlyExempt[account]) revert NotPermanentlyExempt(account);
    isPermanentlyExempt[account] = false;
    emit PermanentExemptionRemoved(account);
}
```

This mirrors the M-15 recommendation: just as linked tophats should be able to have their `eligibility` address changed upon linking/unlinking, permanently exempt addresses should be revocable by a sufficiently privileged role (admin, not just manager).

### Proof of Concept

1. Manager calls `addPermanentExemptions([victimAddress])`.
   - `isPermanentlyExempt[victimAddress] = true` — irreversible.
2. `victimAddress` is later compromised and begins draining rsETH.
3. Admin calls `blockUserTransfers([victimAddress])`:
   - Line 168: `if (isPermanentlyExempt[account] ...) continue;` — silently skipped, no block applied.
4. Admin calls `recoverFrozenFunds(victimAddress)`:
   - Line 210: `if (isPermanentlyExempt[from]) revert AddressPermanentlyExempt(from);` — hard revert.
5. Admin has no on-chain path to freeze or recover rsETH held by `victimAddress`. The emergency mechanism is permanently disabled for this address. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/RSETH.sol (L130-154)
```text
    /// @notice Permanently add accounts to the exempted list (non-reversible)
    /// @param accounts Accounts to mark as permanently exempt (cannot have transfers blocked)
    function addPermanentExemptions(address[] calldata accounts) external onlyLRTManager {
        uint256 length = accounts.length;

        for (uint256 i = 0; i < length; ++i) {
            address account = accounts[i];
            UtilLib.checkNonZeroAddress(account);

            // Ensure the account is not currently blocked
            uint256 blockedUntil = transfersBlockedUntil[account];
            if (blockedUntil != 0) {
                if (block.timestamp < blockedUntil) {
                    revert CannotPermanentlyExemptBlockedAddress(account, blockedUntil);
                }
                // Auto-clean up expired block
                delete transfersBlockedUntil[account];
            }

            if (!isPermanentlyExempt[account]) {
                isPermanentlyExempt[account] = true;
                emit PermanentExemptionAdded(account);
            }
        }
    }
```

**File:** contracts/RSETH.sol (L165-168)
```text
        for (uint256 i = 0; i < length; ++i) {
            address account = accounts[i];

            if (isPermanentlyExempt[account] || account == address(0)) continue;
```

**File:** contracts/RSETH.sol (L206-210)
```text
    function recoverFrozenFunds(address from) external onlyLRTAdmin {
        UtilLib.checkNonZeroAddress(from);
        UtilLib.checkNonZeroAddress(custodyAddress);

        if (isPermanentlyExempt[from]) revert AddressPermanentlyExempt(from);
```

**File:** contracts/RSETH.sol (L294-296)
```text
    function _enforceNotBlocked(address account) internal {
        // Addresses that are permanently exempt can never be blocked
        if (isPermanentlyExempt[account]) return;
```
