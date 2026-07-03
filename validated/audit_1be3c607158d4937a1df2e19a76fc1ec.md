### Title
Blocked User Cannot Execute `instantWithdrawal` Due to `_enforceNotBlocked` in `burnFrom` — (`contracts/RSETH.sol`, `contracts/LRTWithdrawalManager.sol`)

---

### Summary

`LRTWithdrawalManager.instantWithdrawal` calls `IRSETH.burnFrom(msg.sender, amount)` to destroy the user's rsETH. `RSETH.burnFrom` unconditionally calls `_enforceNotBlocked(account)` before burning. If the caller's address has an active entry in `transfersBlockedUntil`, the call reverts with `TransfersBlocked`, making instant withdrawal completely inaccessible for the blocked user for up to 24 hours.

---

### Finding Description

**Call chain:**

```
user → LRTWithdrawalManager.instantWithdrawal (line 229)
         → IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked)
              → RSETH.burnFrom (line 245-248)
                   → _enforceNotBlocked(account) (line 246)
                        → revert TransfersBlocked(account, blockedUntil)  ← if block active
```

**`RSETH.burnFrom`** (lines 245–248): [1](#0-0) 

**`_enforceNotBlocked`** (lines 294–306): [2](#0-1) 

**`instantWithdrawal` burn call** (line 229): [3](#0-2) 

**`blockUserTransfers`** sets `transfersBlockedUntil[account] = block.timestamp + 1 days` (lines 161–177): [4](#0-3) 

The `_enforceNotBlocked` guard is applied to **burns** in addition to transfers and mints. The function name `blockUserTransfers` and its NatSpec ("Block transfers TO and FROM given users for 24 hours") suggest the intent is to freeze token movements, but the side effect is that it also blocks the `instantWithdrawal` redemption path — a feature explicitly designed to let users exit immediately when enabled.

There is no bypass or exemption path inside `instantWithdrawal` for blocked users. The `isPermanentlyExempt` mapping can exempt an address from ever being blocked, but it cannot be applied retroactively once a block is active (the `addPermanentExemptions` function explicitly rejects currently-blocked addresses): [5](#0-4) 

---

### Impact Explanation

A user whose address is in `transfersBlockedUntil` with `block.timestamp < blockedUntil` cannot call `instantWithdrawal` for any asset. Their rsETH is effectively unredeemable via the instant path for up to 24 hours. This matches **Medium — Temporary freezing of funds**.

---

### Likelihood Explanation

The `blockUserTransfers` function is callable by any address holding `LRT_MANAGER` role — a legitimate operational role, not a compromised one. The scenario (manager blocks a user for compliance/AML reasons while instant withdrawal is enabled) is realistic and requires no key compromise or external dependency failure. The block duration is bounded at 24 hours per call (re-applying refreshes, not extends, the window).

---

### Recommendation

In `RSETH.burnFrom`, skip the `_enforceNotBlocked` check when the caller is the `LRTWithdrawalManager` contract (i.e., when the burn is initiated as part of a user redemption), or introduce a separate `burnFromForWithdrawal` entry point that bypasses the transfer-block guard. Alternatively, document that blocking a user intentionally prevents instant withdrawal and ensure the manager is aware of this side effect before applying a block.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.27;

// Foundry fork test (local/fork, no mainnet)
// 1. Deploy/configure RSETH and LRTWithdrawalManager
// 2. Mint rsETH to `user`
// 3. Enable instant withdrawal for the asset
// 4. Manager calls RSETH.blockUserTransfers([user])
// 5. Assert instantWithdrawal reverts with TransfersBlocked

function test_blockedUserCannotInstantWithdraw() public {
    address user = address(0xBEEF);
    uint256 amount = 1 ether;

    // Setup: mint rsETH to user, enable instant withdrawal
    vm.prank(minter);
    rsETH.mint(user, amount);

    vm.prank(manager);
    withdrawalManager.setInstantWithdrawalEnabled(asset, true);

    // Block the user
    address[] memory accounts = new address[](1);
    accounts[0] = user;
    vm.prank(manager);
    rsETH.blockUserTransfers(accounts);

    // Attempt instant withdrawal — must revert
    vm.prank(user);
    vm.expectRevert(
        abi.encodeWithSelector(RSETH.TransfersBlocked.selector, user, block.timestamp + 1 days)
    );
    withdrawalManager.instantWithdrawal(asset, amount, "");
}
```

### Citations

**File:** contracts/RSETH.sol (L140-147)
```text
            uint256 blockedUntil = transfersBlockedUntil[account];
            if (blockedUntil != 0) {
                if (block.timestamp < blockedUntil) {
                    revert CannotPermanentlyExemptBlockedAddress(account, blockedUntil);
                }
                // Auto-clean up expired block
                delete transfersBlockedUntil[account];
            }
```

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

**File:** contracts/RSETH.sol (L245-248)
```text
    function burnFrom(address account, uint256 amount) external onlyRole(LRTConstants.BURNER_ROLE) whenNotPaused {
        _enforceNotBlocked(account);
        _burn(account, amount);
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

**File:** contracts/LRTWithdrawalManager.sol (L229-229)
```text
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
```
