### Title
Unrestricted `initializeWormhole` / `initialize` Allows Attacker to Seize Admin Role and Forge Signature Authority — (File: evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol)

---

### Summary
`OmniBridgeWormhole.initializeWormhole` and its base `OmniBridge.initialize` carry no access-control guard beyond OpenZeppelin's `initializer` modifier, which only prevents re-initialization. If the proxy is deployed in two separate transactions (proxy deploy, then initialize), any unprivileged attacker who observes the pending initialization transaction can front-run it, supply attacker-controlled parameters, claim `DEFAULT_ADMIN_ROLE`, and set `nearBridgeDerivedAddress` to an address they control — giving them the power to forge every signature gate in the bridge.

---

### Finding Description

`OmniBridge.initialize` is declared `public initializer` with no role or ownership check: [1](#0-0) 

`OmniBridgeWormhole.initializeWormhole` is declared `external initializer` with the same absence of access control: [2](#0-1) 

The `initializer` modifier from OpenZeppelin's `Initializable` only prevents the function from being called a second time; it places no restriction on *who* may call it first. When an `ERC1967Proxy` is deployed with empty `data` (i.e., initialization is deferred to a subsequent transaction), the proxy's `_initialized` slot is `0` and the function is callable by anyone.

An attacker who monitors the mempool (or back-runs the proxy-deploy transaction on chains without a public mempool) can call `initializeWormhole` before the protocol team does, supplying:

- An attacker-controlled `nearBridgeDerivedAddress` — the address against which **every** `deployToken` and `finTransfer` signature is verified: [3](#0-2) 

- An attacker-controlled `tokenImplementationAddress` — the logic contract cloned for every new bridge token.

Because `initialize` unconditionally grants `DEFAULT_ADMIN_ROLE` and `PAUSABLE_ADMIN_ROLE` to `_msgSender()`: [4](#0-3) 

the attacker simultaneously becomes the sole admin of the proxy.

---

### Impact Explanation

With `nearBridgeDerivedAddress` set to an attacker-controlled key the attacker can:

1. **Forge `finTransfer` signatures** — `finTransfer` mints or transfers any token to any recipient after verifying only that `ECDSA.recover(hash, sig) == nearBridgeDerivedAddress`. With their own key as the trusted signer, the attacker can mint unbounded wrapped tokens or drain any ERC-20 held by the bridge. [5](#0-4) 

2. **Forge `deployToken` signatures** — the attacker can register arbitrary token mappings, poisoning the `nearToEthToken` / `ethToNearToken` tables. [6](#0-5) 

3. **Retain permanent admin control** — as `DEFAULT_ADMIN_ROLE` holder the attacker can upgrade the proxy, pause/unpause, and block any recovery attempt.

This satisfies the **Critical** impact tier: unauthorized creation and custody escape of wrapped bridge assets through verification failure.

---

### Likelihood Explanation

- The attack requires only a standard front-run (or back-run on L2) of a single publicly visible transaction — no privileged access, no leaked keys.
- The window exists whenever the deployment script separates proxy creation from initialization into two transactions, which is a common pattern.
- The attacker needs no prior capital or protocol interaction.

---

### Recommendation

1. **Atomic initialization**: Deploy the proxy with the initialization calldata encoded in the `ERC1967Proxy` constructor's `data` argument so that initialization occurs in the same transaction as proxy creation, eliminating the front-running window.

2. **Add an access-control guard**: Add an `onlyOwner` or equivalent check to `initialize` / `initializeWormhole`, analogous to the fix applied in the referenced Radiant Capital PR #177. For example, use a two-step pattern where the implementation's constructor sets a deployer address that is the only account permitted to call `initialize`.

3. **Use `_disableInitializers` on the proxy-level**: Ensure that any intermediate state between proxy deployment and initialization is impossible by enforcing atomic deployment in CI/CD and deployment scripts.

---

### Proof of Concept

```
1. Protocol team deploys ERC1967Proxy pointing to OmniBridgeWormhole implementation,
   with empty `data` (initialization deferred).

2. Protocol team broadcasts initializeWormhole(
       legitimateTokenImpl,
       legitimateMPCAddress,
       chainId,
       wormholeAddr,
       consistencyLevel
   ).

3. Attacker observes the pending tx in the mempool and front-runs with:
   initializeWormhole(
       attackerTokenImpl,
       attackerControlledAddress,   // replaces nearBridgeDerivedAddress
       chainId,
       wormholeAddr,
       consistencyLevel
   )

4. Attacker's tx mines first:
   - _msgSender() == attacker → attacker receives DEFAULT_ADMIN_ROLE
   - nearBridgeDerivedAddress = attackerControlledAddress

5. Protocol team's tx reverts (initializer already called).

6. Attacker signs a finTransfer payload with their private key,
   calls finTransfer() → bridge mints arbitrary tokens to attacker.
``` [2](#0-1) [7](#0-6)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L72-86)
```text
    function initialize(
        address tokenImplementationAddress_,
        address nearBridgeDerivedAddress_,
        uint8 omniBridgeChainId_
    ) public initializer {
        tokenImplementationAddress = tokenImplementationAddress_;
        nearBridgeDerivedAddress = nearBridgeDerivedAddress_;
        omniBridgeChainId = omniBridgeChainId_;

        __UUPSUpgradeable_init();
        __AccessControl_init();
        __Pausable_init_unchained();
        _grantRole(DEFAULT_ADMIN_ROLE, _msgSender());
        _grantRole(PAUSABLE_ADMIN_ROLE, _msgSender());
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L149-153)
```text
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L279-312)
```text
    function finTransfer(
        bytes calldata signatureData,
        BridgeTypes.TransferMessagePayload calldata payload
    ) external payable whenNotPaused(PAUSED_FIN_TRANSFER) {
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;

        bytes memory borshEncoded = bytes.concat(
            bytes1(uint8(BridgeTypes.PayloadType.TransferMessage)),
            Borsh.encodeUint64(payload.destinationNonce),
            bytes1(payload.originChain),
            Borsh.encodeUint64(payload.originNonce),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.tokenAddress),
            Borsh.encodeUint128(payload.amount),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.recipient),
            bytes(payload.feeRecipient).length == 0 // None or Some(String) in rust
                ? bytes("\x00")
                : bytes.concat(
                    bytes("\x01"),
                    Borsh.encodeString(payload.feeRecipient)
                ),
            bytes(payload.message).length == 0
                ? bytes("")
                : Borsh.encodeBytes(payload.message)
        );
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L32-46)
```text
    function initializeWormhole(
        address tokenImplementationAddress,
        address nearBridgeDerivedAddress,
        uint8 omniBridgeChainId,
        address wormholeAddress,
        uint8 consistencyLevel
    ) external initializer {
        initialize(
            tokenImplementationAddress,
            nearBridgeDerivedAddress,
            omniBridgeChainId
        );
        _wormhole = IWormhole(wormholeAddress);
        _consistencyLevel = consistencyLevel;
    }
```
