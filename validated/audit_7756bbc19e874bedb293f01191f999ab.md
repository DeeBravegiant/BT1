### Title
Signature Replay in `allowPushers` Lets Creator Nullify Pusher's Revocation — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers` accepts a pusher's EIP-191 consent signature but never marks it as consumed. After a pusher calls `revokePusher()`, the creator can replay the original signature (within the same deadline window) to silently re-establish delegation, making the pusher's revocation permanently ineffective until the deadline expires.

---

### Finding Description

`allowPushers` verifies the pusher's signature and writes `namespaceRemapping[pusher] = msg.sender`:

```solidity
// CompressedOracle.sol L192-211
function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
    _ensureDeadline(deadline);
    ...
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
    );
    require(pusher == ECDSA.recover(hash, signatures[i]));
    namespaceRemapping[pusher] = msg.sender;   // ← no used-signature guard
    ...
}
```

`revokePusher` zeroes the mapping:

```solidity
// CompressedOracle.sol L238-243
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
```

Because the signature is never invalidated, the creator retains the original `(deadline, pusher, creator)` tuple and can call `allowPushers` again with the identical signature. The hash recomputes identically, `ECDSA.recover` succeeds, and `namespaceRemapping[pusher]` is overwritten back to the creator — undoing the revocation without any new consent from the pusher.

The code's own NatSpec acknowledges the deadline is the *only* replay guard:

> "the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."

The deadline is a time bound, not a one-time-use guard. Within the deadline window the signature is reusable an unlimited number of times.

---

### Impact Explanation

After the pusher revokes, any subsequent `fallback()` push they make is routed through `namespaceRemapping[msg.sender]`:

```solidity
// CompressedOracle.sol L315-316
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
```

If the creator has replayed the signature, `namespaceRemapping[pusher]` is non-zero again, so the pusher's data lands in the creator's namespace instead of their own. This corrupts the creator's live oracle slots — the exact slots consumed by `IPriceProvider.getBidAndAskPrice` during pool swaps. A pool anchored to a feed in that namespace will execute swaps against a price the pusher did not intend to publish for the creator, satisfying the "bad-price execution" impact gate.

---

### Likelihood Explanation

- The creator already holds the signature (they used it to establish the first delegation).
- The deadline is a future timestamp chosen by the creator at signing time; long-lived deadlines (days/weeks) are normal operational practice.
- No on-chain state change is required by the attacker beyond calling `allowPushers` again — a single transaction.
- The pusher has no way to detect or prevent the replay short of waiting for the deadline to expire.

---

### Recommendation

Track consumed signatures with a `mapping(bytes32 => bool) private _usedDelegations` and revert if the hash has already been seen:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(!_usedDelegations[hash], "signature already used");
require(pusher == ECDSA.recover(hash, signatures[i]));
_usedDelegations[hash] = true;
namespaceRemapping[pusher] = msg.sender;
```

Alternatively, include a per-pusher nonce in the signed payload so each consent is unique and cannot be replayed even within the same deadline window.

---

### Proof of Concept

1. Pusher signs consent: `keccak256(abi.encode(chainid, oracle, deadline=T+7days, pusher, creator))`.
2. Creator calls `allowPushers(T+7days, [pusher], [sig])` → `namespaceRemapping[pusher] = creator`. ✓
3. Pusher calls `revokePusher()` → `namespaceRemapping[pusher] = address(0)`. ✓
4. Creator calls `allowPushers(T+7days, [pusher], [sig])` again with the **same** `sig`.
   - `_ensureDeadline(T+7days)` passes (still in the future).
   - `hash` recomputes identically.
   - `ECDSA.recover(hash, sig) == pusher` passes.
   - `namespaceRemapping[pusher] = creator` is written again.
5. Pusher's revocation is silently undone. Any subsequent `fallback()` push by the pusher lands in the creator's namespace, corrupting the oracle feed consumed by any pool using that feed. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-191)
```text
    /// @notice Delegates pusher wallets into the caller's namespace. The pusher's EIP-191
    ///         signature is REQUIRED — without it anyone could remap a foreign pusher
    ///         wallet into their own namespace and silently swallow its pushes. The
    ///         deadline is likewise required: the signed consent carries no timestamp of
    ///         its own, so an undated signature could re-establish a delegation AFTER the
    ///         pusher revoked it.
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L192-212)
```text
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L314-317)
```text

        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;

```
