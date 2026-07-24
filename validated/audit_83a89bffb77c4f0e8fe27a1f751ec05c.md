### Title
Cross-Chain Replay of `deploy_token` Signed Payload Due to Missing Chain-ID Binding — (`evm/src/omni-bridge/contracts/OmniBridge.sol`, `starknet/src/bridge_types.cairo`, `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`, `aptos/sources/bridge_types.move`)

---

### Summary

The `MetadataPayload` borsh encoding used to authorize `deploy_token` across all four bridge chains (EVM, Starknet, Solana, Aptos) omits any chain-identity binding. Because the same NEAR MPC-derived address signs for every chain, a valid `deploy_token` signature produced for chain A is byte-identical to what any other chain would accept, enabling an unprivileged attacker to replay it on chain B and trigger unauthorized token deployment.

---

### Finding Description

`fin_transfer` correctly binds its signed payload to the destination chain by interleaving `omniBridgeChainId` (as the OmniAddress tag) twice in the borsh encoding — once before `tokenAddress` and once before `recipient`: [1](#0-0) 

The same pattern is replicated on Starknet (`to_borsh(chain_id)`) and Solana/Aptos (`SOLANA_OMNI_BRIDGE_CHAIN_ID` written before each OmniAddress field): [2](#0-1) [3](#0-2) [4](#0-3) 

`deploy_token` (`MetadataPayload`) does **not** follow this pattern. Its borsh encoding on every chain is:

```
PayloadType::Metadata (1 byte) | token | name | symbol | decimals
```

No chain_id, no contract address, no deployment salt — anywhere: [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) 

The signature is verified against the single shared NEAR MPC derived address stored in each chain's config: [9](#0-8) [10](#0-9) [11](#0-10) [12](#0-11) 

Because the borsh bytes are identical across all chains for the same `(token, name, symbol, decimals)` tuple, a single ECDSA signature over that hash is valid on every chain simultaneously.

---

### Impact Explanation

**High — cross-domain replay of an insufficiently-bound authorization that bypasses the `deploy_token` execution gate.**

Concrete effects:

1. **Unauthorized premature deployment.** An attacker observing a `deploy_token` transaction on chain A (or its mempool) can immediately replay the same `(payload, signature)` on chains B, C, D before NEAR has decided to support those chains. NEAR loses control over the timing and sequencing of token deployment across its bridge network.

2. **Permanent griefing / DoS of legitimate deployment.** Each chain enforces a one-time-only guard (`ERR_TOKEN_EXIST` / `ERR_TOKEN_ALREADY_DEPLOYED`). Once the attacker's replay lands, NEAR's own subsequent `deploy_token` call for that chain is permanently rejected: [13](#0-12) [14](#0-13) [15](#0-14) 

3. **Decimal / accounting divergence.** Each chain normalizes decimals differently (EVM caps at 18, Aptos caps at 8). If NEAR intended to deploy a token with chain-specific parameters on chain B, the attacker forces chain A's raw `decimals` value onto chain B. The resulting normalized decimals may differ from what NEAR's accounting model expects, breaking the backing guarantee for that token on chain B.

---

### Likelihood Explanation

**Medium-High.** The attack requires only:
- Watching any public chain for a `deploy_token` transaction (or its emitted `DeployToken` event)
- Submitting the same `(payload, signature)` to any other chain's bridge contract

No privileged key, no leaked secret, no colluding party. The attacker is a fully unprivileged external observer. The window is open for the entire lifetime of the signature (no expiry on `deploy_token`), and the attack is permanent once executed.

---

### Recommendation

Bind the `MetadataPayload` borsh encoding to the destination chain, mirroring the existing `TransferMessagePayload` pattern. The minimal fix is to prepend the destination `chain_id` byte to the metadata borsh encoding on every chain:

**EVM** (`OmniBridge.sol`, `deployToken`):
```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
+   bytes1(omniBridgeChainId),          // chain binding
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

Apply the same change to `MetadataPayloadTrait::to_borsh` (Starknet), `DeployTokenPayload::serialize_for_near` (Solana), and `metadata_to_borsh` (Aptos). The NEAR MPC signing service must include the target `chain_id` when constructing the payload to sign.

---

### Proof of Concept

1. NEAR MPC signs `MetadataPayload { token: "usdc.near", name: "USD Coin", symbol: "USDC", decimals: 6 }` for Ethereum (chain_id = 2). The borsh bytes are:

   ```
   01 | len("usdc.near") | "usdc.near" | len("USD Coin") | "USD Coin" | len("USDC") | "USDC" | 06
   ```

2. Attacker observes the Ethereum `deployToken` transaction and extracts `(payload, signatureData)`.

3. Attacker calls `deploy_token(signatureData, payload)` on the Starknet bridge (chain_id = 3). The Starknet `_verify_borsh_signature` computes `keccak256` of the identical borsh bytes and recovers the same NEAR MPC address — verification passes: [16](#0-15) 

4. `ERR_TOKEN_ALREADY_DEPLOYED` is not triggered (token not yet on Starknet). A bridge token is deployed on Starknet and registered in `near_to_starknet_token`.

5. NEAR's legitimate `deploy_token` for Starknet now permanently reverts with `ERR_TOKEN_ALREADY_DEPLOYED`. If NEAR intended different decimals for Starknet, the token is now permanently misconfigured.

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

**File:** starknet/src/bridge_types.cairo (L61-84)
```text
    fn to_borsh(self: @TransferMessagePayload, chain_id: u8) -> ByteArray {
        let mut borsh_bytes: ByteArray = Default::default();
        borsh_bytes.append_byte(PayloadType::TransferMessage.into());
        borsh_bytes.append(@borsh::encode_u64(*self.destination_nonce));
        borsh_bytes.append_byte(*self.origin_chain);
        borsh_bytes.append(@borsh::encode_u64(*self.origin_nonce));
        borsh_bytes.append_byte(chain_id);
        borsh_bytes.append(@borsh::encode_address(*self.token_address));
        borsh_bytes.append(@borsh::encode_u128(*self.amount));
        borsh_bytes.append_byte(chain_id);
        borsh_bytes.append(@borsh::encode_address(*self.recipient));
        match self.fee_recipient {
            Option::None => { borsh_bytes.append_byte(0); },
            Option::Some(fee_recipient) => {
                borsh_bytes.append_byte(1);
                borsh_bytes.append(@borsh::encode_byte_array(fee_recipient));
            },
        }
        match self.message {
            Option::None => {},
            Option::Some(message) => { borsh_bytes.append(@borsh::encode_byte_array(message)); },
        }
        borsh_bytes
    }
```

**File:** solana/programs/bridge_token_factory/src/state/message/finalize_transfer.rs (L29-36)
```rust
        // 3. token
        writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;
        params.0.serialize(&mut writer)?;
        // 4. amount
        self.amount.serialize(&mut writer)?;
        // 5. recipient
        writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;
        params.1.serialize(&mut writer)?;
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

**File:** solana/programs/bridge_token_factory/src/state/message/deploy_token.rs (L16-27)
```rust
impl Payload for DeployTokenPayload {
    type AdditionalParams = ();

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

**File:** starknet/src/omni_bridge.cairo (L202-205)
```text
        fn deploy_token(ref self: ContractState, signature: Signature, payload: MetadataPayload) {
            assert(!_is_paused(@self, PAUSE_DEPLOY_TOKEN), 'ERR_DEPLOY_TOKEN_PAUSED');

            _verify_borsh_signature(ref self, @payload.to_borsh(), signature);
```

**File:** starknet/src/omni_bridge.cairo (L207-209)
```text
            let token_id_hash = compute_keccak_byte_array(@payload.token);
            let existing_token = self.near_to_starknet_token.read(token_id_hash);
            assert(existing_token.is_zero(), 'ERR_TOKEN_ALREADY_DEPLOYED');
```

**File:** starknet/src/omni_bridge.cairo (L398-406)
```text
    fn _verify_borsh_signature(
        ref self: ContractState, borsh_bytes: @ByteArray, signature: Signature,
    ) {
        let message_hash_le = compute_keccak_byte_array(borsh_bytes);
        let message_hash = reverse_u256_bytes(message_hash_le);

        let sig = signature_from_vrs(signature.v, signature.r, signature.s);
        verify_eth_signature(message_hash, sig, self.omni_bridge_derived_address.read());
    }
```

**File:** solana/programs/bridge_token_factory/src/state/message/mod.rs (L23-47)
```rust
impl<P: Payload> SignedPayload<P> {
    pub fn verify_signature(
        &self,
        params: P::AdditionalParams,
        derived_near_bridge_address: &[u8; 64],
    ) -> Result<()> {
        let serialized = self.payload.serialize_for_near(params)?;
        let hash = keccak::hash(&serialized);

        let signature_bytes = &self.signature[0..64];

        let signature = libsecp256k1::Signature::parse_standard_slice(signature_bytes)
            .map_err(|_| ProgramError::InvalidArgument)?;
        require!(!signature.s.is_high(), ErrorCode::MalleableSignature);

        let signer = secp256k1_recover(&hash.to_bytes(), self.signature[64], signature_bytes)
            .map_err(|_| error!(ErrorCode::SignatureVerificationFailed))?;

        require!(
            signer.0 == *derived_near_bridge_address,
            ErrorCode::SignatureVerificationFailed
        );

        Ok(())
    }
```

**File:** aptos/sources/omni_bridge.move (L356-372)
```text
    public entry fun deploy_token(
        signature_rs: vector<u8>,
        signature_v: u8,
        token: String,
        name: String,
        symbol: String,
        decimals: u8
    ) {
        let state = &mut BridgeState[bridge_object_address()];
        assert!(
            (state.pause_flags & PAUSE_DEPLOY_TOKEN) == 0,
            E_DEPLOY_TOKEN_PAUSED
        );

        let payload = bridge_types::new_metadata_payload(token, name, symbol, decimals);
        let encoded = payload.metadata_to_borsh();
        verify_signature(state, encoded, signature_rs, signature_v);
```

**File:** aptos/sources/omni_bridge.move (L374-378)
```text
        let token_id = payload.metadata_token();
        assert!(
            !state.near_to_aptos_token.contains(token_id),
            E_TOKEN_ALREADY_DEPLOYED
        );
```
