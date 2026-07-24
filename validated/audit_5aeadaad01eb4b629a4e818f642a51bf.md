### Title
Cross-Chain Replay of `deployToken` MPC Signature Permanently Blocks Token Deployment - (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

### Summary
The `MetadataPayload` borsh-encoded hash used to verify the MPC signature in `deployToken` omits the destination chain ID. Because the same `nearBridgeDerivedAddress` MPC key signs for all supported chains, a valid `deployToken` signature obtained on one chain (e.g., Ethereum) can be replayed verbatim on any other chain (Arbitrum, Base, Starknet, etc.), deploying the wrapped token there without MPC authorization for that chain. Once replayed, the token mapping is permanently set and the legitimate deployment path for that token on that chain is irreversibly blocked.

### Finding Description
In `OmniBridge.sol::deployToken`, the borsh-encoded message hashed for ECDSA verification is:

```
PayloadType.Metadata | token | name | symbol | decimals
``` [1](#0-0) 

The `omniBridgeChainId` field — stored in the contract and used to distinguish chains — is **not included** in this hash. The identical omission exists in the Starknet implementation: [2](#0-1) 

And in the Aptos implementation: [3](#0-2) 

This is in direct contrast to `finTransfer`, where `omniBridgeChainId` is explicitly interleaved twice into the borsh encoding to prevent cross-chain replay: [4](#0-3) 

The comment in `bridge_types.move` even documents this intent for `TransferMessagePayload` — "preventing cross-chain replay" — but the same protection is absent from `MetadataPayload`: [5](#0-4) 

All EVM chains (Ethereum, Arbitrum, Base, BNB, Polygon, HyperEvm, Abs) and Starknet share the same `nearBridgeDerivedAddress` signer. A single MPC-signed `MetadataPayload` for token `wrap.near` on Ethereum produces a signature that passes `ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress` on every other chain identically. [6](#0-5) 

### Impact Explanation
An attacker who observes a valid `deployToken` transaction on chain A can immediately replay the same `(signatureData, metadata)` calldata on chains B, C, D, etc. On each replayed chain:

1. A `BridgeToken` proxy is deployed.
2. `nearToEthToken[metadata.token]` is permanently set to the attacker-controlled proxy address.
3. `isBridgeToken[proxy] = true` is set. [7](#0-6) 

Any subsequent legitimate `deployToken` call for the same NEAR token on that chain reverts with `ERR_TOKEN_EXIST`: [8](#0-7) 

There is no permissionless `removeToken` function for bridge tokens — only `removeCustomToken` exists and it is admin-gated. The attacker-deployed proxy has no minting capability (since `finTransfer` requires a chain-specific MPC signature), so users can never receive bridged tokens for that asset on that chain. The token's bridging path to that chain is permanently frozen.

This satisfies: **Critical — Irreversible fund lock / permanently unclaimable user value** and **High — Insufficiently-bound MPC signature that bypasses the deployment execution gate**.

### Likelihood Explanation
The attack requires only:
1. Watching the mempool or block history on any one chain for a `deployToken` transaction.
2. Submitting the same calldata to `deployToken` on any other chain.

No privileged access, no leaked keys, no colluding parties. The attacker pays only gas. The window is open from the moment the first `deployToken` is broadcast until the token is deployed on every other chain. For tokens that are deployed on chains sequentially (e.g., Ethereum first, then Arbitrum weeks later), the window can be days or weeks.

### Recommendation
Include `omniBridgeChainId` in the `MetadataPayload` borsh encoding, mirroring the pattern already used in `TransferMessagePayload`:

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
+   bytes1(omniBridgeChainId),          // bind to this chain
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

Apply the same fix to `MetadataPayloadTrait::to_borsh()` in `starknet/src/bridge_types.cairo` and `metadata_to_borsh()` in `aptos/sources/bridge_types.move`. The NEAR MPC signing path must also include the target chain ID when constructing the payload to sign.

### Proof of Concept
1. MPC signs `MetadataPayload { token: "wrap.near", name: "Wrapped NEAR", symbol: "wNEAR", decimals: 24 }` for Ethereum deployment. Signature `sig` is produced.
2. Relayer calls `OmniBridge(ethereum).deployToken(sig, payload)` — succeeds, token deployed on Ethereum.
3. Attacker calls `OmniBridge(arbitrum).deployToken(sig, payload)` with the identical `sig` and `payload`.
4. `ECDSA.recover(keccak256(borshEncoded), sig)` returns `nearBridgeDerivedAddress` — identical hash, identical signature, passes.
5. A `BridgeToken` proxy is deployed on Arbitrum; `nearToEthToken["wrap.near"]` is set to the attacker's proxy.
6. When the protocol later attempts to legitimately deploy `wrap.near` on Arbitrum, the call reverts with `ERR_TOKEN_EXIST`.
7. `wrap.near` bridging to Arbitrum is permanently blocked. [9](#0-8) [10](#0-9) [2](#0-1)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L142-153)
```text
        bytes memory borshEncoded = bytes.concat(
            bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
            Borsh.encodeString(metadata.token),
            Borsh.encodeString(metadata.name),
            Borsh.encodeString(metadata.symbol),
            bytes1(metadata.decimals)
        );
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L155-158)
```text
        require(
            !isBridgeToken[nearToEthToken[metadata.token]],
            "ERR_TOKEN_EXIST"
        );
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L190-192)
```text
        isBridgeToken[address(bridgeTokenProxy)] = true;
        ethToNearToken[address(bridgeTokenProxy)] = metadata.token;
        nearToEthToken[metadata.token] = address(bridgeTokenProxy);
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L289-309)
```text
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
```

**File:** starknet/src/bridge_types.cairo (L36-44)
```text
    fn to_borsh(self: @MetadataPayload) -> ByteArray {
        let mut borsh_bytes: ByteArray = Default::default();
        borsh_bytes.append_byte(PayloadType::Metadata.into());
        borsh_bytes.append(@borsh::encode_byte_array(self.token));
        borsh_bytes.append(@borsh::encode_byte_array(self.name));
        borsh_bytes.append(@borsh::encode_byte_array(self.symbol));
        borsh_bytes.append_byte(*self.decimals);
        borsh_bytes
    }
```

**File:** aptos/sources/bridge_types.move (L104-113)
```text
    /// Borsh encoding of `MetadataPayload`. Byte-identical to Starknet / EVM.
    public fun metadata_to_borsh(self: &MetadataPayload): vector<u8> {
        let buf = vector[];
        buf.push_back(PAYLOAD_TYPE_METADATA);
        buf.append(borsh::encode_string(&self.token));
        buf.append(borsh::encode_string(&self.name));
        buf.append(borsh::encode_string(&self.symbol));
        buf.push_back(self.decimals);
        buf
    }
```

**File:** aptos/sources/bridge_types.move (L115-118)
```text
    /// Borsh encoding of `TransferMessagePayload`. Byte-identical to
    /// Starknet / EVM. `chain_id` is interleaved as the OmniAddress tag
    /// before each of `token_address` and `recipient` and is bound into
    /// the signed hash (not the payload), preventing cross-chain replay.
```

**File:** starknet/src/omni_bridge.cairo (L202-209)
```text
        fn deploy_token(ref self: ContractState, signature: Signature, payload: MetadataPayload) {
            assert(!_is_paused(@self, PAUSE_DEPLOY_TOKEN), 'ERR_DEPLOY_TOKEN_PAUSED');

            _verify_borsh_signature(ref self, @payload.to_borsh(), signature);

            let token_id_hash = compute_keccak_byte_array(@payload.token);
            let existing_token = self.near_to_starknet_token.read(token_id_hash);
            assert(existing_token.is_zero(), 'ERR_TOKEN_ALREADY_DEPLOYED');
```
