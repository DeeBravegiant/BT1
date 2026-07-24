### Title
Cross-Chain Replay of `deploy_token` MPC Signature Due to Missing Chain-ID Binding in `MetadataPayload` Hash - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`, `starknet/src/bridge_types.cairo`, `aptos/sources/bridge_types.move`, `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`)

---

### Summary

The `MetadataPayload` Borsh encoding used to verify the MPC signature in `deploy_token` contains no destination chain identifier. A single MPC signature obtained for one chain is cryptographically valid on every other chain that shares the same `nearBridgeDerivedAddress`. An unprivileged attacker can observe a legitimate `deploy_token` transaction on any chain and replay the signature on every other supported chain, deploying the token at an unregistered address and permanently blocking legitimate deployment on those chains.

---

### Finding Description

Every destination chain verifies `deploy_token` by hashing a Borsh-encoded `MetadataPayload` and recovering the signer with raw `ecrecover` / `secp256k1_recover`. The hash input is identical across all chains:

**EVM** (`OmniBridge.sol:142-149`):
```
[PayloadType.Metadata (1 byte)] ++ encodeString(token) ++ encodeString(name) ++ encodeString(symbol) ++ decimals
``` [1](#0-0) 

**Starknet** (`bridge_types.cairo:36-44`):
```
[PayloadType::Metadata] ++ encode_byte_array(token) ++ encode_byte_array(name) ++ encode_byte_array(symbol) ++ decimals
``` [2](#0-1) 

**Aptos** (`bridge_types.move:105-113`):
```
[PAYLOAD_TYPE_METADATA] ++ encode_string(token) ++ encode_string(name) ++ encode_string(symbol) ++ decimals
``` [3](#0-2) 

**Solana** (`deploy_token.rs:19-27`):
```
[IncomingMessageType::Metadata] ++ borsh(token, name, symbol, decimals)
``` [4](#0-3) 

None of these encodings include a chain ID, contract address, or any domain separator. The NEAR side also omits chain binding when requesting the MPC signature:

```rust
let metadata_payload = MetadataPayload {
    prefix: PayloadType::Metadata,
    token: token_id.to_string(),
    name: metadata.name,
    symbol: metadata.symbol,
    decimals: metadata.decimals,
};
let payload = near_sdk::env::keccak256_array(
    borsh::to_vec(&metadata_payload)...
);
``` [5](#0-4) 

This is in direct contrast to `TransferMessagePayload`, which correctly embeds `chain_id` twice (as the OmniAddress tag before `token_address` and before `recipient`) in all implementations: [6](#0-5) [7](#0-6) 

---

### Impact Explanation

An attacker who observes a valid `deploy_token(sig, payload)` call on chain A can submit the identical `(sig, payload)` to `deploy_token` on chains B, C, D, etc. Each chain will:

1. Compute `keccak256(borsh([Metadata, token, name, symbol, decimals]))` — identical bytes on every chain.
2. Recover the signer — same result everywhere.
3. Accept the signature as valid.
4. Deploy a new bridge token contract at a chain-specific address that NEAR has never registered.

Because each chain's `deploy_token` guard (`ERR_TOKEN_EXIST` / `ERR_TOKEN_ALREADY_DEPLOYED`) is a one-way latch, once the token is deployed at the attacker-chosen moment, legitimate deployment is permanently blocked on that chain. NEAR will never sign `fin_transfer` payloads for the orphaned token address, so the wrapped token is permanently unbacked and the bridging path for that asset on the replayed chain is irreversibly frozen.

**Scoped impact**: Irreversible fund lock / permanently unclaimable user value (bridging path for the affected token on the replayed chain is permanently closed); and acceptance of cross-domain signatures that bypass execution gates.

---

### Likelihood Explanation

- All `deploy_token` calls are public on-chain transactions; the signature is visible in calldata to any observer.
- The attacker needs only to copy the calldata and submit it to the same function on a different chain — no special knowledge, capital, or privilege required.
- The bridge is live on Ethereum, Arbitrum, Base, BNB, Starknet, Solana, and Aptos simultaneously, all sharing the same `nearBridgeDerivedAddress`. Every new token deployment is an opportunity to replay across all other chains.
- Likelihood: **High**.

---

### Recommendation

Bind the `MetadataPayload` hash to the destination chain by including the chain ID in the Borsh encoding, mirroring the existing pattern used by `TransferMessagePayload`. On the NEAR signing side, pass the destination `ChainKind` into `log_metadata_callback` and include it in the serialized payload before requesting the MPC signature. On each destination chain, include `omniBridgeChainId` / `chain_id` / `SOLANA_OMNI_BRIDGE_CHAIN_ID` in the hash input for `deploy_token`, exactly as is already done for `fin_transfer`.

---

### Proof of Concept

1. NEAR MPC signs `MetadataPayload { prefix: Metadata, token: "usdc.near", name: "USD Coin", symbol: "USDC", decimals: 6 }` for deployment on Ethereum (chain 0). The resulting signature `sig` is broadcast in the Ethereum `deployToken` transaction.

2. Attacker observes the transaction and extracts `(sig, payload)` from calldata.

3. Attacker calls `deployToken(sig, payload)` on Arbitrum (chain 3). The EVM contract computes:
   ```
   keccak256([0x01] ++ borsh("usdc.near") ++ borsh("USD Coin") ++ borsh("USDC") ++ 0x06)
   ```
   This is byte-for-byte identical to the Ethereum hash. `ECDSA.recover` returns `nearBridgeDerivedAddress`. The check passes. [8](#0-7) 

4. A new `BridgeToken` proxy is deployed on Arbitrum at an address NEAR has never registered. `nearToEthToken["usdc.near"]` is now set on Arbitrum.

5. Any future legitimate attempt by NEAR to deploy USDC on Arbitrum hits `require(!isBridgeToken[nearToEthToken[metadata.token]], "ERR_TOKEN_EXIST")` and reverts permanently. [9](#0-8) 

6. The same replay works identically against Starknet (`deploy_token`), Aptos (`deploy_token`), and Solana (`deploy_token`) using the same signature.

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

**File:** aptos/sources/bridge_types.move (L105-113)
```text
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

**File:** aptos/sources/bridge_types.move (L119-131)
```text
    public fun transfer_message_to_borsh(
        self: &TransferMessagePayload, chain_id: u8
    ): vector<u8> {
        let buf = vector[];
        buf.push_back(PAYLOAD_TYPE_TRANSFER_MESSAGE);
        buf.append(bcs::to_bytes(&self.destination_nonce));
        buf.push_back(self.origin_chain);
        buf.append(bcs::to_bytes(&self.origin_nonce));
        buf.push_back(chain_id);
        buf.append(bcs::to_bytes(&self.token_address));
        buf.append(bcs::to_bytes(&self.amount));
        buf.push_back(chain_id);
        buf.append(bcs::to_bytes(&self.recipient));
```

**File:** solana/programs/bridge_token_factory/src/state/message/deploy_token.rs (L19-27)
```rust
    fn serialize_for_near(&self, _params: Self::AdditionalParams) -> Result<Vec<u8>> {
        let mut writer = BufWriter::new(Vec::with_capacity(DEFAULT_SERIALIZER_CAPACITY));
        IncomingMessageType::Metadata.serialize(&mut writer)?;
        self.serialize(&mut writer)?; // borsh encoding
        writer
            .into_inner()
            .map_err(|_| error!(ErrorCode::InvalidArgs))
    }
}
```

**File:** near/omni-bridge/src/lib.rs (L345-354)
```rust
        let metadata_payload = MetadataPayload {
            prefix: PayloadType::Metadata,
            token: token_id.to_string(),
            name: metadata.name,
            symbol: metadata.symbol,
            decimals: metadata.decimals,
        };

        let payload = near_sdk::env::keccak256_array(
            borsh::to_vec(&metadata_payload).near_expect(BridgeError::Borsh),
```
