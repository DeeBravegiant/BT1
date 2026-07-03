Audit Report

## Title
Block Stuffing Enables Gap Window in Transfer Block Expiry, Allowing Blocked Address to Transfer rsETH — (`contracts/RSETH.sol`)

## Summary
`blockUserTransfers` sets a hard 24-hour expiry on transfer blocks with no on-chain renewal mechanism. If an attacker sustains block stuffing long enough to delay the manager's refresh transaction past `blockedUntil`, `_enforceNotBlocked` auto-clears the mapping and the blocked address can freely transfer rsETH. `recoverFrozenFunds` also becomes unavailable during this gap because it requires an active block.

## Finding Description
`blockUserTransfers` writes `transfersBlockedUntil[account] = block.timestamp + 1 days` and emits an event, but provides no on-chain mechanism to extend the block without a new manager transaction landing on-chain. [1](#0-0) 

`_enforceNotBlocked` checks `block.timestamp < blockedUntil`; when the timestamp reaches or exceeds `blockedUntil` it deletes the mapping entry and returns without reverting: [2](#0-1) 

`_transfer` calls `_enforceNotBlocked` for both `from` and `to`, so once the block expires the blocked address can call `transfer`/`transferFrom` freely: [3](#0-2) 

`recoverFrozenFunds` independently checks `blockedUntil == 0 || block.timestamp >= blockedUntil` and reverts with `NoActiveTransferBlock`, so the admin recovery path is also closed during the gap: [4](#0-3) 

**Exploit flow:**
1. Manager calls `blockUserTransfers([target])` — `transfersBlockedUntil[target] = T + 1 days`.
2. Attacker (or the blocked address itself) fills every block with maximum-gas transactions starting near `T + 1 days`, excluding the manager's refresh transaction.
3. Once `block.timestamp >= T + 1 days`, `_enforceNotBlocked` no longer reverts; the blocked address calls `transfer` and moves its entire rsETH balance.
4. Manager's refresh lands after the transfer — too late.

## Impact Explanation
**Low — Block stuffing.** The blocked address can transfer its entire rsETH balance during the gap window. Even a single block gap (~12 s on mainnet) is sufficient to drain the balance. `recoverFrozenFunds` is simultaneously unavailable, removing the admin's fallback. The impact class "Low. Block stuffing." is explicitly listed in the allowed scope.

## Likelihood Explanation
The attack is economically rational only when the value of rsETH held by the blocked address exceeds the cost of stuffing blocks for the duration of the manager's monitoring and submission latency. On Ethereum mainnet this cost is tens of ETH per minute, limiting the attack to large holders. However, the gap is also reachable without block stuffing if the manager's transaction is delayed by ordinary network congestion or operational error, making the design fragile regardless of attacker capability.

## Recommendation
1. **Rolling window on attempted transfer**: in `_enforceNotBlocked`, when `block.timestamp < blockedUntil`, reset `transfersBlockedUntil[account] = block.timestamp + 1 days` so every attempted transfer by the blocked address extends the block on-chain without requiring a manager transaction.
2. **Grace period for `recoverFrozenFunds`**: allow the admin to call `recoverFrozenFunds` for a short window (e.g., 1 hour) after `blockedUntil` has passed, providing a fallback even if the refresh is delayed.
3. **Early off-chain alert**: emit a dedicated event (e.g., `TransferBlockExpiringSoon`) at `blockedUntil - 1 hour` so monitoring infrastructure can trigger a refresh with sufficient lead time, shrinking the stuffing window the attacker must sustain.

## Proof of Concept
The submitted Foundry test is valid and minimal. `vm.warp(blockedUntil)` correctly simulates the effect of block stuffing (manager refresh excluded from all blocks until expiry). The test demonstrates:
- `rseth.transfer(other, balance)` succeeds at `block.timestamp == blockedUntil` (block expired, `_enforceNotBlocked` clears and returns).
- A subsequent `blockUserTransfers` refresh lands after the transfer, confirming the gap is exploitable.

A complete fork test should: deploy `RSETH` against a mainnet fork, grant roles, mint rsETH to `target`, call `blockUserTransfers`, `vm.warp` to `blockedUntil`, assert `transfer` succeeds, and assert `recoverFrozenFunds` reverts with `NoActiveTransferBlock` at the same timestamp.

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
