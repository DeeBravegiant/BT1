### Title
Pusher Delegation Consent Signature Can Be Replayed After `revokePusher()` to Re-Establish Namespace Write Authority — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers()` in `CompressedOracleV1` accepts a pusher's EIP-191 consent signature that is bound to `(chainid, contract, deadline, pusher, creator)` but contains **no nonce**. After a pusher calls `revokePusher()` to clear `namespaceRemapping[pusher]`, the creator can immediately replay the original, still-valid signature to `allowPushers()` and restore `namespaceRemapping[pusher] = creator`. The pusher's revocation is silently undone for the entire remaining lifetime of the deadline window.

---

### Finding Description

`allowPushers()` verifies the pusher's consent signature and writes `namespaceRemapping[pusher] = msg.sender`:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;   // ← state set
```

`revokePusher()` clears that mapping:

```solidity
namespaceRemapping[msg.sender] = address(0);   // ← state cleared
```

Because the consent hash contains no per-delegation nonce, the same `(deadline, pusher, creator)` tuple produces the same hash every time. The creator retains the original signature bytes and can call `allowPushers()` again with identical arguments, passing `_ensureDeadline(deadline)` as long as `block.timestamp <= deadline`, and the mapping is restored to `creator`.

The code comment on `allowPushers()` explicitly acknowledges the deadline as the intended mitigation:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."*

But the deadline only prevents replay **after** it expires. Within the window `[now, deadline]` — which callers routinely set days or weeks in the future — the creator can replay the signature an unlimited number of times, making `revokePusher()` effectively a no-op.

---

### Impact Explanation

After revocation is undone, the pusher's subsequent `fallback()` pushes are silently redirected back into the creator's namespace:

```solidity
// fallback() — namespace resolution
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
```

The pusher believes they are writing to their own namespace (because they revoked), but the data lands in the creator's namespace. Any pool whose `IPriceProvider` reads from the creator's `CompressedOracle` feeds will consume this misdirected data. If the pusher's intent after revocation was to stop feeding the creator's namespace (e.g., due to a dispute or key compromise), the creator can silently continue capturing those price updates, potentially driving bad-price execution in live swaps that depend on those feeds.

---

### Likelihood Explanation

- The creator holds the original signature bytes off-chain (they submitted them in the first `allowPushers()` call).
- Deadlines are typically set days to weeks in the future (the test suite uses `block.timestamp + 1 days`).
- The replay call is a single permissionless transaction with no cost beyond gas.
- The pusher has no on-chain mechanism to invalidate the old signature before the deadline expires.

---

### Recommendation

Add a per-pusher nonce to the consent hash and increment it on every successful `allowPushers()` call (or on every `revokePusher()` / `removePushers()` call). Include the nonce in the signed message:

```solidity
mapping(address => uint256) public pusherNonce;

// in allowPushers():
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender,
        pusherNonce[pusher]   // ← add nonce
    ))
);
// after successful delegation:
pusherNonce[pusher]++;
```

Any signature produced before the nonce was incremented becomes permanently invalid, so a revoked delegation cannot be re-established without a fresh signature from the pusher.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent with deadline = now + 1 days
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

// 2. Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator);  // delegated

// 3. Pusher self-revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0));  // revoked

// 4. Creator replays the SAME signature — revocation undone
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator);  // ← delegation restored without pusher's consent

// 5. Pusher's next push lands in creator's namespace, not their own
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(slotId, pos, raw, tsMs));
assertTrue(ok);
// data is in creator's namespace, not pusher's
assertGt(oracle.getOracleData(oracle.feedIdOf(creator, slotId, pos)).price, 0);
assertEq(oracle.getOracleData(oracle.feedIdOf(pusher,  slotId, pos)).price, 0);
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-212)
```text
    /// @notice Delegates pusher wallets into the caller's namespace. The pusher's EIP-191
    ///         signature is REQUIRED — without it anyone could remap a foreign pusher
    ///         wallet into their own namespace and silently swallow its pushes. The
    ///         deadline is likewise required: the signed consent carries no timestamp of
    ///         its own, so an undated signature could re-establish a delegation AFTER the
    ///         pusher revoked it.
    function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
        _ensureDeadline(deadline);

        uint256 l = pushers.length;
        require(l == signatures.length);
        for (uint256 i; i < l; i++) {
            address pusher = pushers[i];

            if (pusher == msg.sender) {
                revert NoSelfRemapping();
            }

            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
            emit PusherAuthorized(pusher, msg.sender);
        }
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L236-243)
```text
    /// @notice Allows a pusher to self-revoke their delegation. After revocation the
    ///         wallet pushes into its OWN namespace again (the registrationless default).
    function revokePusher() external {
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
        namespaceRemapping[msg.sender] = address(0);
        emit PusherRevoked(msg.sender, creator);
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L311-320)
```text
    fallback() override external {
        uint256 end;
        uint256 namespace;

        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;

        assembly ("memory-safe") {
            end := calldatasize()
            namespace := shl(96, creator) // [creator:20][zeros:12]
```
