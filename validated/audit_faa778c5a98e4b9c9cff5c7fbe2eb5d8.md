Audit Report

## Title
Pusher delegation signature can be replayed within the deadline window to override a pusher's self-revocation — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

## Summary

`CompressedOracleV1.allowPushers` accepts a pusher's EIP-191 consent signature and unconditionally writes `namespaceRemapping[pusher] = msg.sender` with no guard against reuse of a previously-consumed signature. After a pusher calls `revokePusher()`, the creator can submit the identical `(deadline, [pusher], [sig])` tuple a second time before the deadline expires, silently re-establishing the delegation the pusher intended to exit. The pusher's own-namespace feed then stops receiving updates, its timestamp freezes, and any pool whose price provider is bound to that feed will revert on every swap with `FeedStalled`.

## Finding Description

`allowPushers` (L192–211) performs only two checks before writing the mapping:

```solidity
function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
    _ensureDeadline(deadline);
    ...
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
    );
    require(pusher == ECDSA.recover(hash, signatures[i]));
    namespaceRemapping[pusher] = msg.sender;   // ← unconditional overwrite, no used-sig guard
``` [1](#0-0) 

`revokePusher` (L238–243) clears the mapping:

```solidity
function revokePusher() external {
    ...
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [2](#0-1) 

Because the signed payload `(chainid, oracle, deadline, pusher, creator)` contains no nonce or revocation counter, and no `mapping(bytes32 => bool) _usedSigs` exists anywhere in the contract, the creator can re-submit the exact same calldata after the pusher revokes. `_ensureDeadline` passes (deadline not yet expired), `ECDSA.recover` returns the pusher address (signature is still cryptographically valid), and the mapping is overwritten back to `creator`.

The code comment at L186–191 explicitly names the deadline as the sole replay mitigation, but the deadline only prevents replay *after* expiry, not *within* the window: [3](#0-2) 

After re-delegation, `fallback()` routes the pusher's pushes back into the creator's namespace:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

The pusher's own-namespace storage slot (`feedIdOf(pusher, slotIndex, positionIndex)`) receives no further writes; its embedded 56-bit timestamp freezes at the last pre-revocation value.

## Impact Explanation

Any `PriceProvider` (or `PriceProviderL2`, `ProtectedPriceProvider`, `AnchoredPriceProvider`) whose `offchainFeedId` encodes the pusher's own namespace will read a frozen `refTime`. The staleness check in every provider variant returns the `(0, type(uint128).max)` sentinel:

```solidity
if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA)) {
    return (0, type(uint128).max);
}
``` [5](#0-4) 

`getBidAndAskPrice()` then reverts with `FeedStalled`, which propagates through `_getBidAndAskPriceX64` into `MetricOmmPool.swap`, making every swap on the affected pool revert. This is broken core pool functionality — the swap flow is rendered permanently unusable until the pusher re-delegates or the pool admin changes the price provider. [6](#0-5) 

## Likelihood Explanation

- The creator retains the original signature bytes trivially: they submitted the `allowPushers` transaction and can read it from on-chain calldata history at zero cost.
- The deadline must not have expired. Delegation deadlines are typically hours to days, giving a wide replay window.
- The pusher must operate their own-namespace feed (the normal production configuration for a self-operating pusher).

All three conditions are realistic in normal operation. The creator has a clear economic motive: they lose price-feed routing if the pusher exits.

## Recommendation

Track consumed signatures with a `mapping(bytes32 => bool) private _usedDelegationSigs` keyed on the signature hash (or on a tuple that uniquely identifies the delegation intent):

```solidity
bytes32 sigKey = keccak256(abi.encode(deadline, pusher, msg.sender));
require(!_usedDelegationSigs[sigKey], "signature already used");
_usedDelegationSigs[sigKey] = true;
```

Alternatively, include a per-pusher nonce in the signed payload (`namespaceRemappingNonce[pusher]`) and increment it on every successful delegation or revocation, making each consent signature single-use regardless of deadline.

## Proof of Concept

```
t=0  Pusher signs: keccak256(abi.encode(chainid, oracle, deadline, pusher, creator))
t=1  Creator calls allowPushers(deadline, [pusher], [sig])
       → namespaceRemapping[pusher] = creator  ✓

t=2  Pusher calls revokePusher()
       → namespaceRemapping[pusher] = address(0)  ✓
       Pusher's fallback() now writes to own namespace.

t=3  Creator calls allowPushers(deadline, [pusher], [sig])  ← SAME sig, deadline not expired
       → _ensureDeadline passes (deadline > block.timestamp)
       → ECDSA.recover returns pusher  (sig still cryptographically valid)
       → namespaceRemapping[pusher] = creator  ← revocation silently undone

t=4  Pusher's fallback() pushes land in creator namespace again.
     feedIdOf(pusher, slotIndex, positionIndex) timestamp frozen.
     PriceProvider._isStale() → true → (0, type(uint128).max)
     MetricOmmPool.swap() → FeedStalled revert on every call.
```

Foundry test outline:
1. Deploy `CompressedOracleV1` and a `PriceProvider` bound to `feedIdOf(pusher, 0, 0)`.
2. Pusher signs delegation payload; creator calls `allowPushers`.
3. Pusher calls `revokePusher()`; assert `namespaceRemapping[pusher] == address(0)`.
4. Creator replays identical `allowPushers` call; assert `namespaceRemapping[pusher] == creator`.
5. Warp past `MAX_TIME_DELTA`; call `getBidAndAskPrice()` and assert `FeedStalled` revert.

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L192-211)
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
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L238-243)
```text
    function revokePusher() external {
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
        namespaceRemapping[msg.sender] = address(0);
        emit PusherRevoked(msg.sender, creator);
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-316)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L197-200)
```text
        // 2. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA)) {
            return (0, type(uint128).max);
        }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L227-228)
```text
    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();
```
