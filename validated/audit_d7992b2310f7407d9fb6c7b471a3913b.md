### Title
Stale `pendingStateGuard` Not Cleared in `purgeStateGuardRole` Allows Unauthorized Oracle Role Takeover — (File: `smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

---

### Summary

`purgeStateGuardRole` deletes `stateGuard[feedId]` but never clears `pendingStateGuard[feedId]`. A previously nominated pending guard can call the permissionless `acceptStateGuardRole` after the current guard has purged itself, gaining unauthorized `stateGuard` control and the ability to manipulate price bounds for any feed consumed by Metric OMM pools.

---

### Finding Description

`OracleBase` implements a two-step guard-transfer pattern:

1. Current guard calls `setStateGuardRole(feedId, newGuard)` → writes `pendingStateGuard[feedId] = newGuard`.
2. Nominee calls `acceptStateGuardRole(feedId)` → clears `pendingStateGuard` and promotes itself to `stateGuard`.

A separate function, `purgeStateGuardRole`, is intended to remove the current guard entirely and return authority to `ADMIN_ROLE`: [1](#0-0) 

```solidity
function purgeStateGuardRole(bytes32 feedId) external checkRole(feedId) {
    delete stateGuard[feedId];
    emit StateGuardDeleted(feedId);
}
```

It deletes only `stateGuard[feedId]`. It does **not** delete `pendingStateGuard[feedId]`.

Compare with `acceptStateGuardRole`, which correctly clears both: [2](#0-1) 

```solidity
function acceptStateGuardRole(bytes32 feedId) external {
    require(pendingStateGuard[feedId] == msg.sender, InvalidGuard(msg.sender));
    delete pendingStateGuard[feedId];
    stateGuard[feedId] = msg.sender;
    emit StateGuardUpdated(feedId, msg.sender);
}
```

`acceptStateGuardRole` has no access control beyond the `pendingStateGuard` check. If `pendingStateGuard[feedId]` is still set after `purgeStateGuardRole`, the nominee can call `acceptStateGuardRole` at any time and become the new `stateGuard`.

The `checkRole` modifier confirms that after `purgeStateGuardRole`, authority falls back to `ADMIN_ROLE`: [3](#0-2) 

```solidity
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

But the stale `pendingStateGuard` bypasses this: the nominee calls `acceptStateGuardRole` (no `checkRole` gate) and installs itself as `stateGuard` without ADMIN consent.

---

### Impact Explanation

The `stateGuard` role controls `setPriceGuard`, which sets the `[min, max]` price bounds enforced on every oracle price push for a feed: [4](#0-3) 

An unauthorized `stateGuard` can:
1. Call `setPriceGuard(feedId, 1, type(uint128).max)` — effectively disabling the price guard, allowing any price (stale, inverted, or manipulated) to be stored and subsequently consumed by Metric OMM pools via the `price(feedId, pool)` path.
2. Call `setStateGuardRole` to nominate a further address, permanently entrenching unauthorized control.

A pool consuming this feed will execute swaps at the unclamped bad price, causing swap conservation failure or direct loss of trader principal — matching the "bad-price execution" and "admin-boundary break" impact criteria.

---

### Likelihood Explanation

The trigger sequence is realistic:

1. Guard A nominates B via `setStateGuardRole(feedId, B)`.
2. B becomes compromised or A changes its mind.
3. A calls `purgeStateGuardRole(feedId)` believing this cancels the pending nomination.
4. B front-runs or races ADMIN's `purgePendingStateGuardRole` call and calls `acceptStateGuardRole(feedId)`.

Step 3 is the natural "undo" action a guard would take; the missing `delete pendingStateGuard` makes it incomplete. ADMIN has a window to call `purgePendingStateGuardRole`, but this race is not atomic and may not be known to ADMIN if the nomination was not publicly announced.

---

### Recommendation

In `purgeStateGuardRole`, also delete `pendingStateGuard[feedId]`:

```diff
function purgeStateGuardRole(bytes32 feedId) external checkRole(feedId) {
    delete stateGuard[feedId];
+   delete pendingStateGuard[feedId];
    emit StateGuardDeleted(feedId);
}
```

This mirrors the fix recommended in the reference report (deleting `_pendingOwner` in the second step of `renounceOwnership`) and ensures that removing the active guard atomically cancels any in-flight nomination.

---

### Proof of Concept

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
assertEq(oracle.stateGuard(feedId), address(B)); // B is now guard

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
