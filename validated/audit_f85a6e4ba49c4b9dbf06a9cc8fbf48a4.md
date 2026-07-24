### Title
Missing Chain-ID Binding in `deployToken` Signature Hash Enables Cross-Chain Replay — (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

---

### Summary

The `deployToken` signed-message hash omits the destination chain identifier (`omniBridgeChainId`) that `finTransfer` includes. Because the same NEAR MPC-derived address (`nearBridgeDerivedAddress`) is used across every EVM deployment, a valid `deployToken` signature produced for chain A is cryptographically valid on chain B. An unprivileged attacker who observes a public `deployToken` transaction can replay it on any other OmniBridge EVM instance, deploying the token at an attacker-chosen time and permanently blocking the legitimate deployment on that chain.

---

### Finding Description

**Bug class (analog to the report):** Insufficient transcript/message binding — a parameter that distinguishes execution context (chain ID) is present in one message type but absent in another, leaving the signature under-bound.

**EVM — `OmniBridge.sol` `deployToken`**

The borsh-encoded message that is hashed and verified against `nearBridgeDerivedAddress` is:

```
PayloadType.Metadata | token | name | symbol | decimals
```

No chain identifier is included. [1](#0-0) 

By contrast, `finTransfer` encodes `omniBridgeChainId` **twice** (once for the token chain slot, once for the recipient chain slot):

```
PayloadType.TransferMessage | destinationNonce | originChain | originNonce
  | omniBridgeChainId | tokenAddress | amount
  | omniBridgeChainId | recipient | feeRecipient | message
``` [2](#0-1) 

**Starknet — `bridge_types.cairo`**

`MetadataPayload::to_borsh()` produces the identical chain-ID-free layout:

```
PayloadType::Metadata | token | name | symbol | decimals
``` [3](#0-2) 

`TransferMessagePayload::to_borsh(chain_id)` correctly injects `chain_id` twice: [4](#0-3) 

The Starknet bridge calls `payload.to_borsh()` (no chain ID) for `deploy_token` but `payload.to_borsh(self.omni_bridge_chain_id.read())` for `fin_transfer`: [5](#0-4) [6](#0-5) 

**Solana — `deploy_token.rs`**

`DeployTokenPayload::serialize_for_near` serialises `IncomingMessageType::Metadata` followed by the token fields — no `SOLANA_OMNI_BRIDGE_CHAIN_ID`: [7](#0-6) 

`FinalizeTransferPayload::serialize_for_near` writes `SOLANA_OMNI_BRIDGE_CHAIN_ID` twice: [8](#0-7) 

The omission is structurally identical across all three implementations: `deployToken` messages are not bound to any specific chain.

---

### Impact Explanation

**Impact: High** — Acceptance of insufficiently-bound signatures that bypass execution gates.

A replayed `deployToken` call on chain B:

1. Passes signature verification (same `nearBridgeDerivedAddress`, same hash).
2. Deploys a bridge-token proxy at a non-deterministic address chosen by the attacker's transaction ordering.
3. Writes `isBridgeToken[proxy] = true` and `nearToEthToken[proxy] = metadata.token`, registering the attacker-timed address as the canonical bridge token for that NEAR token ID on chain B.
4. Permanently blocks the legitimate deployment: the `require(!isBridgeToken[nearToEthToken[metadata.token]], "ERR_TOKEN_EXIST")` guard will revert every future attempt. [9](#0-8) 

Once the wrong address is registered, all subsequent `finTransfer` calls for that token on chain B mint to or release from the attacker-deployed proxy. If the NEAR bridge's prover accepts the `DeployToken` event (it came from the legitimate contract address), the NEAR side will also record the wrong EVM address, creating a permanent asset-identity divergence that breaks backing guarantees.

---

### Likelihood Explanation

**Likelihood: High.**

- The attacker needs only to watch the public mempool or confirmed blocks on any chain where a `deployToken` transaction appears and submit the identical calldata to another chain.
- No privileged access, leaked key, or colluding party is required.
- The same `nearBridgeDerivedAddress` is shared across all EVM deployments (it is derived from the single NEAR MPC key), so the signature is unconditionally valid on every chain.
- The attack is most effective as a front-run on a chain where the token has not yet been deployed, which is a predictable window (new chain onboarding, new token listing).

---

### Recommendation

Include the destination chain identifier in the `deployToken` borsh-encoded message, mirroring the pattern already used in `finTransfer`.

**EVM:**
```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    bytes1(omniBridgeChainId),          // ← add this
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

Apply the equivalent fix to `MetadataPayload::to_borsh()` in `starknet/src/bridge_types.cairo` and `DeployTokenPayload::serialize_for_near` in `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`. The NEAR MPC signing side must be updated simultaneously to include the chain ID when producing the signature.

---

### Proof of Concept

1. NEAR MPC system issues a `deployToken` signature `σ` for token `"usdc.near"` on Ethereum (chain ID `0x01`). The signed bytes are `keccak256(0x01 || borsh("usdc.near") || borsh("USD Coin") || borsh("USDC") || 0x06)`.

2. Attacker observes the Ethereum transaction `deployToken(σ, {token:"usdc.near", name:"USD Coin", symbol:"USDC", decimals:6})`.

3. Attacker submits the identical call to the Arbitrum OmniBridge (chain ID `0x04`) before the NEAR team does.

4. `ECDSA.recover(keccak256(borshEncoded), σ)` returns `nearBridgeDerivedAddress` — the hash is identical because chain ID is absent from `borshEncoded`. [10](#0-9) 

5. A new `BridgeToken` proxy is deployed on Arbitrum at address `X` (attacker-timed). `nearToEthToken["usdc.near"] = X` is written.

6. Any subsequent legitimate `deployToken` call for `"usdc.near"` on Arbitrum reverts with `ERR_TOKEN_EXIST`. [9](#0-8) 

7. All `finTransfer` calls for `"usdc.near"` on Arbitrum now mint from the attacker-timed proxy at `X`, not the address the NEAR team intended, permanently diverging the token registry.

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

**File:** starknet/src/omni_bridge.cairo (L202-210)
```text
        fn deploy_token(ref self: ContractState, signature: Signature, payload: MetadataPayload) {
            assert(!_is_paused(@self, PAUSE_DEPLOY_TOKEN), 'ERR_DEPLOY_TOKEN_PAUSED');

            _verify_borsh_signature(ref self, @payload.to_borsh(), signature);

            let token_id_hash = compute_keccak_byte_array(@payload.token);
            let existing_token = self.near_to_starknet_token.read(token_id_hash);
            assert(existing_token.is_zero(), 'ERR_TOKEN_ALREADY_DEPLOYED');

```

**File:** starknet/src/omni_bridge.cairo (L252-254)
```text
            _verify_borsh_signature(
                ref self, @payload.to_borsh(self.omni_bridge_chain_id.read()), signature,
            );
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

**File:** solana/programs/bridge_token_factory/src/state/message/finalize_transfer.rs (L20-43)
```rust
    fn serialize_for_near(&self, params: Self::AdditionalParams) -> Result<Vec<u8>> {
        let mut writer = BufWriter::new(Vec::with_capacity(DEFAULT_SERIALIZER_CAPACITY));
        // 0. prefix
        IncomingMessageType::InitTransfer.serialize(&mut writer)?;
        // 1. destination_nonce
        self.destination_nonce.serialize(&mut writer)?;
        // 2. transfer_id
        writer.write_all(&[self.transfer_id.origin_chain])?;
        self.transfer_id.origin_nonce.serialize(&mut writer)?;
        // 3. token
        writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;
        params.0.serialize(&mut writer)?;
        // 4. amount
        self.amount.serialize(&mut writer)?;
        // 5. recipient
        writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;
        params.1.serialize(&mut writer)?;
        // 6. fee_recipient
        self.fee_recipient.serialize(&mut writer)?;

        writer
            .into_inner()
            .map_err(|_| error!(ErrorCode::InvalidArgs))
    }
```
