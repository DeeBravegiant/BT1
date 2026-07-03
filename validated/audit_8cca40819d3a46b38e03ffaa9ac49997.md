### Title
Single `LRTAdmin` wallet can seize any rsETH holder's entire token balance via `blockUserTransfers` + `recoverFrozenFunds` - (File: contracts/RSETH.sol)

---

### Summary
`RSETH.sol` exposes a two-step mechanism that allows a single privileged wallet holding `DEFAULT_ADMIN_ROLE` to unilaterally confiscate the full rsETH balance of any non-exempt user. Because `DEFAULT_ADMIN_ROLE` is the OpenZeppelin AccessControl admin of all roles — including `LRTConstants.MANAGER` — the same wallet can self-grant the manager role, block a target user's transfers, set the custody address to an attacker-controlled address, and then drain the target's rsETH balance. No multisig or time-lock is required.

---

### Finding Description
`RSETH.sol` introduces two privileged functions:

**Step 1 — `blockUserTransfers` (manager role)** [1](#0-0) 

Any address holding `LRTConstants.MANAGER` can block transfers to/from an arbitrary non-exempt user for 24 hours.

**Step 2 — `recoverFrozenFunds` (admin role)** [2](#0-1) 

Any address holding `DEFAULT_ADMIN_ROLE` can transfer the **entire** rsETH balance of a currently-blocked address to `custodyAddress` by calling `super._transfer`, bypassing the normal transfer-block enforcement.

**Custody address is also admin-controlled:** [3](#0-2) 

The `onlyLRTAdmin` modifier resolves to `DEFAULT_ADMIN_ROLE` in `LRTConfigRoleChecker`: [4](#0-3) 

In OpenZeppelin `AccessControl`, `DEFAULT_ADMIN_ROLE` is the admin of every role by default, meaning the holder can grant themselves `LRTConstants.MANAGER` at any time. Therefore a **single wallet** can execute all three steps without any external co-signer.

---

### Impact Explanation
rsETH is the protocol's liquid restaking token representing deposited ETH/LSTs. Seizing a user's rsETH balance is equivalent to stealing their proportional claim on the underlying assets held across `LRTDepositPool`, `LRTUnstakingVault`, and EigenLayer strategies. The impact is **Critical — direct theft of user funds at rest**.

---

### Likelihood Explanation
The attack requires no external conditions: no oracle manipulation, no market state, no front-running. It is executable in two transactions by a single EOA that holds `DEFAULT_ADMIN_ROLE`. The only friction is that the block lasts only 24 hours, but `blockUserTransfers` can be re-applied before expiry to refresh the window indefinitely: [5](#0-4) 

Likelihood is **Low** in the sense that it requires a malicious or compromised admin, but the absence of any multisig or time-lock requirement means the attack is trivially executable once the admin key is controlled.

---

### Recommendation
1. Protect `recoverFrozenFunds` and `setCustodyAddress` behind a multisig or on-chain governance time-lock, analogous to the fix applied to `migrateToken` in the reference report.
2. Separate `DEFAULT_ADMIN_ROLE` from the role that can call `blockUserTransfers` so that no single key can both freeze and drain a user's balance.
3. Consider capping `recoverFrozenFunds` to only operate on assets proven to be proceeds of sanctioned/illegal activity, with an independent verification step.

---

### Proof of Concept
```
// Attacker holds DEFAULT_ADMIN_ROLE on LRTConfig

// 1. Self-grant MANAGER role (DEFAULT_ADMIN_ROLE is admin of all roles in OZ AccessControl)
lrtConfig.grantRole(LRTConstants.MANAGER, attacker);

// 2. Block victim's transfers for 24 h
address[] memory victims = new address[](1);
victims[0] = victim;
rsETH.blockUserTransfers(victims);   // onlyLRTManager — now satisfied

// 3. Point custody to attacker wallet
rsETH.setCustodyAddress(attacker);   // onlyLRTAdmin

// 4. Drain victim's entire rsETH balance to attacker
rsETH.recoverFrozenFunds(victim);    // onlyLRTAdmin
// → super._transfer(victim, attacker, balanceOf(victim))
```

All four calls can be made from a single EOA in a single block. The victim's rsETH — representing their proportional claim on all protocol-held ETH/LSTs — is transferred to the attacker with no recourse.

### Citations

**File:** contracts/RSETH.sol (L156-177)
```text
    /// @notice Block transfers TO and FROM given users for 24 hours
    /// @dev Re-applying the block before expiry refreshes the hold to `block.timestamp + 1 days`
    ///      (i.e. not cumulative; never more than 24h from the latest call). Exempt addresses cannot be blocked.
    ///      Emits {UserTransfersBlocked} only when the timestamp changes.
    /// @param accounts Addresses to block.
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

**File:** contracts/RSETH.sol (L199-201)
```text
    function setCustodyAddress(address newCustodyAddress) external onlyLRTAdmin {
        _setCustodyAddress(newCustodyAddress);
    }
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

**File:** contracts/utils/LRTConfigRoleChecker.sol (L58-63)
```text
    modifier onlyLRTAdmin() {
        if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.DEFAULT_ADMIN_ROLE, msg.sender)) {
            revert ILRTConfig.CallerNotLRTConfigAdmin();
        }
        _;
    }
```
