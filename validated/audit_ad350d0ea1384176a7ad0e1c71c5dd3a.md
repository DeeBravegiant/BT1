The code confirms the vulnerability exactly as described. `purgeStateGuardRole` at L120-124 only deletes `stateGuard[feedId]` and never touches `pendingStateGuard[feedId]`, while `acceptStateGuardRole` at L111-118 has no access control beyond the stale `pendingStateGuard` check. `purgePendingStateGuardRole` at L105-109 exists as a separate function but is not called atomically by `purgeStateGuardRole`, creating the race window.

Audit Report

## Title
Stale `pendingStateGuard` Not Cleared in `purgeStateGuardRole` Allows Unauthorized Oracle Role Takeover — (File: `smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

## Summary
`purgeStateGuardRole` deletes `stateGuard[feedId]` but leaves `pendingStateGuard[feedId]` intact. A previously nominated pending guard can call the permissionless `acceptStateGuardRole` after the current guard has purged itself, gaining unauthorized `stateGuard` control without ADMIN consent. This unauthorized guard can then disable price bounds via `setPriceGuard`, allowing stale or manipulated prices to reach Metric OMM pools.

## Finding Description
`OracleBase` implements a two-step guard-transfer pattern. `setStateGuardRole` writes `pendingStateGuard[feedId]` [1](#0-0)  and `acceptStateGuardRole` promotes the pending nominee while clearing `pendingStateGuard`. [2](#0-1) 

`purgeStateGuardRole` only deletes `stateGuard[feedId]` and emits an event — it never touches `pendingStateGuard[feedId]`: [3](#0-2) 

After `purgeStateGuardRole`, the `checkRole` modifier falls back to `ADMIN_ROLE` since `stateGuard[feedId]` is now `address(0)`. [4](#0-3)  However, `acceptStateGuardRole` has no `checkRole` gate — it only checks `pendingStateGuard[feedId] == msg.sender`. [5](#0-4)  Since `pendingStateGuard[feedId]` was never cleared, the stale nominee can call `acceptStateGuardRole` at any time and install itself as `stateGuard` without ADMIN involvement.

`purgePendingStateGuardRole` exists as a separate function that correctly clears `pendingStateGuard[feedId]`, [6](#0-5)  but it is not called by `purgeStateGuardRole` and requires a separate transaction, creating a race window where the nominee can front-run ADMIN.

## Impact Explanation
The unauthorized `stateGuard` can call `setPriceGuard(feedId, 1, type(uint128).max)`, which passes the `minPrice < maxPrice` check and effectively disables the price clamp for the feed. [7](#0-6)  Any price — stale, inverted, or manipulated — then passes the guard and is consumed by Metric OMM pools via the `price(feedId, pool)` path, causing bad-price execution and potential swap conservation failure or direct loss of trader principal. This matches the "bad-price execution" and "admin-boundary break" allowed impact criteria.

## Likelihood Explanation
The trigger sequence is realistic and requires no special privileges beyond the nominee's own address. Guard A nominates B, then calls `purgeStateGuardRole` believing the nomination is cancelled. B (compromised or acting adversarially) front-runs or races ADMIN's `purgePendingStateGuardRole` call. The race is non-atomic: ADMIN must independently discover the stale nomination and act before B does. If the nomination was not publicly announced or ADMIN is not monitoring `pendingStateGuard` state, B wins the race unconditionally.

## Recommendation
In `purgeStateGuardRole`, also delete `pendingStateGuard[feedId]` atomically:

```solidity
function purgeStateGuardRole(bytes32 feedId) external checkRole(feedId) {
    delete stateGuard[feedId];
+   delete pendingStateGuard[feedId];
    emit StateGuardDeleted(feedId);
}
```

This ensures removing the active guard atomically cancels any in-flight nomination, mirroring the correct behavior of `acceptStateGuardRole` which clears both fields. [8](#0-7) 

## Proof of Concept

```solidity
// 1. Guard A nominates B
oracle.setStateGuardRole(feedId, address(B));
assertEq(oracle.pendingStateGuard(feedId), address(B));

// 2. Guard A purges itself (intending to cancel the nomination)
oracle.purgeStateGuardRole(feedId);
assertEq(oracle.stateGuard(feedId), address(0));
// pendingStateGuard is NOT cleared:
assertEq(oracle.pendingStateGuard(feedId), address(B)); // still set

// 3. B accepts — no ADMIN consent required
vm.prank(address(B));
oracle.acceptStateGuardRole(feedId);
assertEq(oracle.stateGuard(feedId), address(B)); // B is now unauthorized guard

// 4. B disables the price guard
vm.prank(address(B));
oracle.setPriceGuard(feedId, 1, type(uint128).max);
// Any price, including stale/manipulated, now passes the guard
// and will be consumed by Metric OMM pools on the next swap
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L65-74)
```text
    modifier checkRole(bytes32 feedId) {
        address _guard = stateGuard[feedId];
        if (_guard != address(0)) {
            require(_guard == msg.sender, InvalidGuard(msg.sender));
        } else {
            _checkRole(ADMIN_ROLE);
        }

        _;
    }
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L88-97)
```text
    function setPriceGuard(bytes32 feedId, uint128 minPrice, uint128 maxPrice)
        external
        checkRole(feedId)
    {
        require(minPrice < maxPrice);

        priceGuard[feedId] = PriceGuard({min: minPrice, max: maxPrice});

        emit PriceGuardUpdated(feedId, minPrice, maxPrice);
    }
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L99-103)
```text
    function setStateGuardRole(bytes32 feedId, address newGuard) external checkRole(feedId) {
        pendingStateGuard[feedId] = newGuard;

        emit StateGuardPending(feedId, newGuard);
    }
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L105-109)
```text
    function purgePendingStateGuardRole(bytes32 feedId) external checkRole(feedId) {
        delete pendingStateGuard[feedId];

        emit PendingStateGuardDeleted(feedId);
    }
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L111-118)
```text
    function acceptStateGuardRole(bytes32 feedId) external {
        require(pendingStateGuard[feedId] == msg.sender, InvalidGuard(msg.sender));

        delete pendingStateGuard[feedId];
        stateGuard[feedId] = msg.sender;

        emit StateGuardUpdated(feedId, msg.sender);
    }
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L120-124)
```text
    function purgeStateGuardRole(bytes32 feedId) external checkRole(feedId) {
        delete stateGuard[feedId];

        emit StateGuardDeleted(feedId);
    }
```
