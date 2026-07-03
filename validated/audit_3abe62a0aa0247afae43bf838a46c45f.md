### Title
Irreversible `isPermanentlyExempt` State Permanently Disables Fund Recovery for Exempt Addresses - (File: contracts/RSETH.sol)

### Summary
`RSETH.addPermanentExemptions` sets `isPermanentlyExempt[account] = true` with no corresponding removal function. Once an address is permanently exempt, the protocol can never block its transfers or invoke `recoverFrozenFunds` on it, even if that address is later compromised or deprecated.

### Finding Description
In `contracts/RSETH.sol`, the `addPermanentExemptions` function (callable by `onlyLRTManager`) marks addresses as permanently exempt from transfer blocks:

```solidity
// Line 130-131 NatSpec: "Permanently add accounts to the exempted list (non-reversible)"
isPermanentlyExempt[account] = true;
```

There is no `removePermanentExemption` function anywhere in the contract. This creates a one-way state transition identical in class to the reported RUSD.sol bug: once the terminal state is entered, it cannot be reversed even if circumstances change.

Two downstream functions permanently break for any exempt address:

1. `blockUserTransfers` silently skips permanently exempt accounts: [1](#0-0) 

2. `recoverFrozenFunds` hard-reverts for permanently exempt accounts: [2](#0-1) 

The `_enforceNotBlocked` internal function also unconditionally returns for exempt addresses, meaning mints, burns, and transfers to/from them bypass all block enforcement permanently: [3](#0-2) 

### Impact Explanation
Legitimate candidates for permanent exemption include bridge contracts (`L1VaultV2`), pool contracts (`RSETHPoolV3`), and wrapper contracts — all of which hold or route user rsETH. If any such contract is later exploited, deprecated, or replaced, the admin has no mechanism to:

- Block transfers to/from the old address (preventing an attacker from draining it freely)
- Call `recoverFrozenFunds` to move rsETH held at the compromised address to the custody address

The fund recovery mechanism — the protocol's only on-chain tool for responding to a compromised rsETH holder — is permanently and irrevocably disabled for every address ever added to `isPermanentlyExempt`. This matches the **Low** impact tier: "Contract fails to deliver promised returns, but doesn't lose value," because the recovery path is permanently closed while the funds remain movable by whoever controls the exempt address.

### Likelihood Explanation
Permanent exemptions are a normal operational action (bridges, pools, and wrappers need them to function). The risk materialises whenever any previously exempted contract is later exploited or deprecated — a realistic scenario given the protocol's cross-chain architecture and upgrade history. No privileged compromise is required to trigger the impact; the manager's original legitimate action is sufficient.

### Recommendation
Add a `removePermanentExemption(address account)` function restricted to `onlyLRTAdmin` (higher privilege than the setter) that clears `isPermanentlyExempt[account]`. This mirrors the pattern used for `blockUserTransfers` (which can both set and implicitly clear via expiry) and restores the protocol's ability to respond to compromised exempt addresses.

### Proof of Concept
1. Manager calls `addPermanentExemptions([bridgeContract])` — a routine operational step.
2. `bridgeContract` is later exploited; attacker controls it and holds rsETH.
3. Admin attempts `blockUserTransfers([bridgeContract])` → silently skipped (line 168).
4. Admin attempts `recoverFrozenFunds(bridgeContract)` → reverts `AddressPermanentlyExempt` (line 210).
5. No on-chain path exists to block or recover the rsETH at `bridgeContract`. The attacker transfers it freely. [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/RSETH.sol (L132-154)
```text
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

**File:** contracts/RSETH.sol (L168-168)
```text
            if (isPermanentlyExempt[account] || account == address(0)) continue;
```

**File:** contracts/RSETH.sol (L206-219)
```text
    function recoverFrozenFunds(address from) external onlyLRTAdmin {
        UtilLib.checkNonZeroAddress(from);
        UtilLib.checkNonZeroAddress(custodyAddress);

        if (isPermanentlyExempt[from]) revert AddressPermanentlyExempt(from);

        uint256 blockedUntil = transfersBlockedUntil[from];
        if (blockedUntil == 0 || block.timestamp >= blockedUntil) revert NoActiveTransferBlock(from);

        uint256 accountBalance = balanceOf(from);

        // Bypass transfer block enforcement when transferring to custody address
        super._transfer(from, custodyAddress, accountBalance);
        emit FrozenFundsRecovered(from, custodyAddress, accountBalance);
```

**File:** contracts/RSETH.sol (L294-302)
```text
    function _enforceNotBlocked(address account) internal {
        // Addresses that are permanently exempt can never be blocked
        if (isPermanentlyExempt[account]) return;

        // Check if the account has an active transfer block
        uint256 blockedUntil = transfersBlockedUntil[account];
        if (blockedUntil == 0) return;

        if (block.timestamp < blockedUntil) revert TransfersBlocked(account, blockedUntil);
```
