### Title
`recoverFrozenFunds` Does Not Extend or Persist the Transfer Block, Allowing a Seized User to Re-Acquire rsETH After Block Expiry - (File: contracts/RSETH.sol)

---

### Summary
`RSETH.recoverFrozenFunds` seizes a blocked user's entire rsETH balance and transfers it to the custody address, but never extends or makes permanent the `transfersBlockedUntil[from]` entry. Once the original 24-hour block expires, the user's restriction is silently auto-deleted and they can freely re-acquire rsETH through `LRTDepositPool`, defeating the purpose of the seizure.

---

### Finding Description
The `blockUserTransfers` function sets `transfersBlockedUntil[account] = block.timestamp + 1 days` — a hard 24-hour window. [1](#0-0) 

`recoverFrozenFunds` requires an active block, then calls `super._transfer` to move the user's full balance to `custodyAddress`. Critically, it performs no write to `transfersBlockedUntil[from]` — neither extending it nor setting it to a sentinel value like `type(uint256).max`. [2](#0-1) 

`_enforceNotBlocked` auto-deletes any expired entry on the next interaction: [3](#0-2) 

After the block expires, `_enforceNotBlocked(to)` passes silently, and `LRTDepositPool._mintRsETH` mints fresh rsETH directly to `msg.sender`: [4](#0-3) 

The user can then call `depositETH` or `depositAsset` on `LRTDepositPool` to receive new rsETH with no restriction: [5](#0-4) 

This is the direct analog of the reported Solana bug: an administrative action (refund / seizure) is executed but the state flag that would prevent the user from re-entering (`is_blacklisted` / `transfersBlockedUntil`) is not updated to enforce a lasting restriction.

---

### Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**

The `recoverFrozenFunds` function is designed to permanently neutralize a malicious rsETH holder. Because the block is not extended after seizure, the user can re-acquire rsETH as soon as the 24-hour window lapses. The admin must manually re-invoke `blockUserTransfers` and `recoverFrozenFunds` in a continuous loop to keep the user out, which is operationally unreliable and creates a recurring window of exposure. No protocol funds are directly stolen, but the security guarantee of the freeze-and-seize mechanism is broken.

---

### Likelihood Explanation
**Medium.**

`recoverFrozenFunds` is an active admin tool intended for use against identified malicious actors. Any time it is invoked, the described gap is present. A sophisticated user who is aware of the 24-hour block mechanism can simply wait out the window and re-deposit, making exploitation straightforward and requiring no special privileges.

---

### Recommendation
After transferring the balance, extend `transfersBlockedUntil[from]` to a far-future timestamp (or `type(uint256).max`) so the user cannot receive or transfer rsETH again without an explicit admin action:

```diff
function recoverFrozenFunds(address from) external onlyLRTAdmin {
    UtilLib.checkNonZeroAddress(from);
    UtilLib.checkNonZeroAddress(custodyAddress);

    if (isPermanentlyExempt[from]) revert AddressPermanentlyExempt(from);

    uint256 blockedUntil = transfersBlockedUntil[from];
    if (blockedUntil == 0 || block.timestamp >= blockedUntil) revert NoActiveTransferBlock(from);

    uint256 accountBalance = balanceOf(from);

+   // Extend the block indefinitely so the user cannot re-acquire rsETH
+   transfersBlockedUntil[from] = type(uint256).max;

    // Bypass transfer block enforcement when transferring to custody address
    super._transfer(from, custodyAddress, accountBalance);
    emit FrozenFundsRecovered(from, custodyAddress, accountBalance);
}
```

Alternatively, consider adding a dedicated permanent-block mapping (separate from the 24-hour `transfersBlockedUntil`) that is set here and checked in `_enforceNotBlocked`, so the admin retains the ability to lift it explicitly if needed.

---

### Proof of Concept

1. Admin calls `blockUserTransfers([alice])` at `T=0` → `transfersBlockedUntil[alice] = T + 1 days`.
2. Admin calls `recoverFrozenFunds(alice)` at `T=1h` → alice's rsETH balance is transferred to `custodyAddress`. `transfersBlockedUntil[alice]` remains `T + 1 days` (unchanged).
3. At `T = 1 days + 1 second`, alice's block has expired. `_enforceNotBlocked` will auto-delete the entry on the next call.
4. Alice calls `LRTDepositPool.depositETH{value: X}(...)`. `_mintRsETH` calls `IRSETH.mint(alice, amount)`. `_enforceNotBlocked(alice)` finds `block.timestamp >= blockedUntil`, deletes the entry, and returns — no revert.
5. Alice now holds fresh rsETH and can transfer it freely, fully bypassing the intended permanent restriction. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/RSETH.sol (L161-176)
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
```

**File:** contracts/RSETH.sol (L206-220)
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

**File:** contracts/LRTDepositPool.sol (L76-93)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L686-690)
```text
    function _mintRsETH(uint256 rsethAmountToMint) private {
        address rsethToken = lrtConfig.rsETH();
        // mint rseth for user
        IRSETH(rsethToken).mint(msg.sender, rsethAmountToMint);
    }
```
