### Title
Missing Chain ID in `deployToken` MetadataPayload Signature Enables Cross-Chain Replay — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

The NEAR MPC signs a `MetadataPayload` for bridge-token deployment that contains no destination chain ID. Any attacker who observes a legitimate `deployToken` transaction on one EVM chain can replay the identical signature on any other EVM chain running `OmniBridge`, pre-deploying the bridge token at an unexpected address and permanently blocking the legitimate deployment on the targeted chain.

---

### Finding Description

**Root cause — signing side (NEAR):**

In `near/omni-bridge/src/lib.rs`, `log_metadata_callback` constructs a `MetadataPayload` containing only `token`, `name`, `symbol`, and `decimals`, then asks the NEAR MPC to sign its keccak256 hash. No destination chain ID is included. [1](#0-0) 

The `MetadataPayload` struct itself has no chain field: [2](#0-1) 

**Root cause — verification side (EVM):**

`OmniBridge.deployToken` reconstructs the same chain-ID-free borsh blob and verifies the MPC signature against it. Compare this with `finTransfer`, which correctly embeds `omniBridgeChainId` twice in the hash:

`deployToken` — **no chain ID in hash**: [3](#0-2) 

`finTransfer` — **chain ID correctly embedded**: [4](#0-3) 

The same gap exists on Starknet. `MetadataPayloadImpl::to_borsh` includes no chain ID: [5](#0-4) 

While `TransferMessagePayloadImpl::to_borsh` correctly injects `chain_id`: [6](#0-5) 

Starknet's `deploy_token` verifies the chain-ID-free borsh blob: [7](#0-6) 

The Aptos `metadata_to_borsh` is identical — no chain ID: [8](#0-7) 

---

### Impact Explanation

An attacker who observes a legitimate `deployToken` call on chain A (e.g., Ethereum) extracts `signatureData` and `metadata` from the public calldata and submits them to `deployToken` on chain B (e.g., Arbitrum). The signature passes `ECDSA.recover` because the borsh-encoded payload is byte-identical. The EVM contract then:

1. Deploys a new `ERC1967Proxy` bridge token on chain B at a nonce-determined address.
2. Sets `nearToEthToken[metadata.token]` on chain B to this attacker-triggered address.
3. Marks the token as `isBridgeToken`.

The NEAR bridge's internal state for chain B has no record of this address (the legitimate `deploy_token` proof flow never ran for chain B). Any subsequent legitimate `deployToken` call on chain B reverts with `ERR_TOKEN_EXIST`. Users who later attempt to bridge this token to chain B will have their source-chain tokens locked in the bridge with no corresponding mint on the destination side, because NEAR cannot resolve the token address for chain B.

This maps to **High — frozen redemption path / potential irreversible fund lock** in the allowed impact scope.

---

### Likelihood Explanation

- Requires only reading public calldata from any EVM chain and submitting a transaction on another — no keys, no privileges, no colluding parties.
- Gas cost only. The attack can be automated to front-run every legitimate `deployToken` across all supported EVM chains simultaneously.
- Multiple production chains (Ethereum, Arbitrum, Base, BNB, Polygon) all run `OmniBridge` with the same `deployToken` interface.

---

### Recommendation

Include the destination chain ID in the signed payload for token deployment, mirroring the existing pattern used in `finTransfer` / `TransferMessagePayload`:

1. **NEAR signing side**: Modify `log_metadata_callback` to accept a `destination_chain` parameter and embed it in `MetadataPayload` before requesting the MPC signature.
2. **EVM verification side**: Add `bytes1(omniBridgeChainId)` to the borsh blob constructed in `deployToken`, so the recovered signer is only valid for the chain that holds the matching `omniBridgeChainId`.
3. Apply the same fix to Starknet's `MetadataPayloadImpl::to_borsh` and Aptos's `metadata_to_borsh`.

---

### Proof of Concept

```
1. Observe a legitimate deployToken tx on Ethereum OmniBridge.
   Extract: signatureData, metadata = (token, name, symbol, decimals)

2. Call on Arbitrum OmniBridge:
   deployToken(signatureData, metadata)

3. Inside deployToken (Arbitrum):
   borshEncoded = [PayloadType.Metadata | encodeString(token) | encodeString(name)
                   | encodeString(symbol) | bytes1(decimals)]
   hashed = keccak256(borshEncoded)          // identical to Ethereum hash
   ECDSA.recover(hashed, signatureData)      // returns nearBridgeDerivedAddress ✓
   → no revert

4. Bridge token for `metadata.token` is deployed on Arbitrum at address X.
   nearToEthToken[metadata.token] = X  (Arbitrum state)

5. Legitimate deployToken on Arbitrum now reverts: ERR_TOKEN_EXIST.
   NEAR bridge has no record of X for Arbitrum.
   Users bridging this token to Arbitrum have source funds locked with no mint path.
```

### Citations

**File:** near/omni-bridge/src/lib.rs (L345-355)
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
        );
```

**File:** near/omni-types/src/lib.rs (L714-722)
```rust
#[near(serializers = [borsh, json])]
#[derive(Debug, Clone)]
pub struct MetadataPayload {
    pub prefix: PayloadType,
    pub token: String,
    pub name: String,
    pub symbol: String,
    pub decimals: u8,
}
```

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L289-313)
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

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }
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

**File:** starknet/src/bridge_types.cairo (L61-71)
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
