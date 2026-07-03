### Title
Block Stuffing Can Create a Gap Window in Transfer Block Refresh, Allowing Blocked Address to Transfer rsETH — (`contracts/RSETH.sol`)

---

### Summary

The `blockUserTransfers` mechanism in `RSETH.sol` relies on the LRT manager submitting a refresh transaction before the 24-hour block expires. An attacker (or the blocked address itself) can use block stuffing to delay that refresh transaction past the expiry timestamp, opening a window during which the blocked address can freely transfer rsETH.

---

### Finding Description

`blockUserTransfers` sets a hard 24-hour expiry: [1](#0-0) 

`_enforceNotBlocked` only reverts while `block.timestamp < blockedUntil`; at or after expiry it silently clears the mapping and returns: [2](#0-1) 

There is no on-chain mechanism to extend the block without a new manager transaction landing. If the manager's refresh call is submitted near the end of the 24-hour window and an attacker fills every block with maximum-gas transactions, the refresh is excluded from those blocks. Once `block.timestamp >= blockedUntil`, `_transfer` no longer reverts for the target address: [3](#0-2) 

The same delay affects the `pause()` call (PAUSER_ROLE) and `recoverFrozenFunds()` (admin), since all three are ordinary transactions that compete for block space.

---

### Impact Explanation

**Low — Block stuffing.** During the gap between block expiry and the manager's refresh landing, the previously blocked address can execute rsETH transfers. The window is bounded by how long the attacker can sustain block stuffing, but even a single block gap (≈12 s on mainnet) is sufficient for the blocked address to move its entire balance. The `recoverFrozenFunds` path also becomes unavailable during the gap because `NoActiveTransferBlock` is thrown once `block.timestamp >= blockedUntil`: [4](#0-3) 

---

### Likelihood Explanation

The attack is economically rational only when the value of rsETH held by the blocked address exceeds the cost of stuffing blocks for the duration of the manager's monitoring latency. On Ethereum mainnet, stuffing even a few minutes of blocks costs tens of ETH, so the attack is only viable for large holders. However, the design offers no on-chain fallback: if the manager's transaction is delayed for any reason (block stuffing, network congestion, operational error), the gap is real and exploitable.

---

### Recommendation

1. **Extend the block on-chain without a new transaction**: store the block as a renewable rolling window (e.g., reset the 24-hour clock on every attempted transfer by the blocked address), so expiry cannot be weaponised.
2. **Add a grace period**: allow `recoverFrozenFunds` to operate for a short window (e.g., 1 hour) after `blockedUntil` has passed, giving the admin a fallback even if the refresh is delayed.
3. **Emit a time-locked event well before expiry** (e.g., at T − 1 hour) so off-chain monitoring can trigger a refresh with sufficient lead time, reducing the stuffing window the attacker must sustain.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Foundry test (local fork or standalone)
// Demonstrates the gap window without any block stuffing infrastructure.

import "forge-std/Test.sol";
import "../contracts/RSETH.sol";

contract BlockRefreshGapTest is Test {
    RSETH rseth;
    address manager = address(0xBEEF);
    address target  = address(0xDEAD);
    address other   = address(0xCAFE);

    function setUp() public {
        // Deploy and initialise (simplified; wire up real LRTConfig in a fork)
        // Grant manager the MANAGER role, mint some rsETH to target
        // ...
    }

    function testGapWindowExploitable() public {
        // 1. Manager blocks target
        vm.prank(manager);
        address[] memory accounts = new address[](1);
        accounts[0] = target;
        rseth.blockUserTransfers(accounts);

        uint256 blockedUntil = rseth.transfersBlockedUntil(target);

        // 2. Warp to exactly the expiry timestamp (block stuffing simulated by vm.warp)
        vm.warp(blockedUntil);

        // 3. Transfer succeeds — block has expired, manager refresh not yet landed
        vm.prank(target);
        rseth.transfer(other, rseth.balanceOf(target));

        // 4. Manager's refresh lands one second later — too late
        vm.warp(blockedUntil + 1);
        vm.prank(manager);
        rseth.blockUserTransfers(accounts); // refreshes, but funds already moved

        assertEq(rseth.balanceOf(target), 0, "target drained during gap");
        assertGt(rseth.balanceOf(other),  0, "other received funds");
    }
}
```

The `vm.warp(blockedUntil)` call simulates the effect of block stuffing: the manager's refresh transaction is excluded from every block until the timestamp reaches `blockedUntil`, at which point `_enforceNotBlocked` no longer reverts and the transfer succeeds.

### Citations

**File:** contracts/RSETH.sol (L161-162)
```text
    function blockUserTransfers(address[] calldata accounts) external onlyLRTManager {
        uint256 blockedUntil = block.timestamp + 1 days;
```

**File:** contracts/RSETH.sol (L212-213)
```text
        uint256 blockedUntil = transfersBlockedUntil[from];
        if (blockedUntil == 0 || block.timestamp >= blockedUntil) revert NoActiveTransferBlock(from);
```

**File:** contracts/RSETH.sol (L287-291)
```text
    function _transfer(address from, address to, uint256 amount) internal override {
        _enforceNotBlocked(from);
        _enforceNotBlocked(to);
        super._transfer(from, to, amount);
    }
```

**File:** contracts/RSETH.sol (L299-305)
```text
        uint256 blockedUntil = transfersBlockedUntil[account];
        if (blockedUntil == 0) return;

        if (block.timestamp < blockedUntil) revert TransfersBlocked(account, blockedUntil);

        // Auto-clean up expired block
        delete transfersBlockedUntil[account];
```
