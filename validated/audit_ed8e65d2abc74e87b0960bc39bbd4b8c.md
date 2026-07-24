### Title
`deployToken` Signature Hash Omits Destination Chain ID, Enabling Cross-Chain Replay — (`evm/src/omni-bridge/contracts/OmniBridge.sol`, `starknet/src/bridge_types.cairo`, `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`)

### Summary
The MPC-signed `MetadataPayload` hash used to authorize `deployToken` across all destination chains does not include any chain identifier. The identical signature is therefore valid on every chain where the Omni Bridge is deployed. An unprivileged attacker who observes the signature (emitted on-chain by NEAR) can replay it on any other chain, deploying the bridged token there without NEAR's per-chain authorization. By contrast, `finTransfer` / `fin_transfer` correctly binds its hash to the destination chain via `omniBridgeChainId`.

### Finding Description

**NEAR signing side** — `near/omni-bridge/src/lib.rs` `log_metadata_callback()` constructs and signs a `MetadataPayload` that contains only `prefix`, `token`, `name`, `symbol`, and `decimals`:

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
```

No destination chain ID is mixed into the payload before it is sent to the MPC signer. [1](#0-0) 

The `MetadataPayload` struct itself confirms the absence of any chain field: [2](#0-1) 

**EVM verification** — `OmniBridge.sol` `deployToken()` hashes only the five metadata fields:

```solidity
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

`omniBridgeChainId` is never included. [3](#0-2) 

Compare with `finTransfer()`, which correctly embeds `omniBridgeChainId` twice (for token address and recipient address): [4](#0-3) 

**Starknet verification** — `MetadataPayload.to_borsh()` serializes only the five metadata fields with no chain ID:

```cairo
fn to_borsh(self: @MetadataPayload) -> ByteArray {
    borsh_bytes.append_byte(PayloadType::Metadata.into());
    borsh_bytes.append(@borsh::encode_byte_array(self.token));
    borsh_bytes.append(@borsh::encode_byte_array(self.name));
    borsh_bytes.append(@borsh::encode_byte_array(self.symbol));
    borsh_bytes.append_byte(*self.decimals);
    borsh_bytes
}
``` [5](#0-4) 

`deploy_token()` calls `_verify_borsh_signature(ref self, @payload.to_borsh(), signature)` — no chain ID argument. [6](#0-5) 

`fin_transfer()` passes `self.omni_bridge_chain_id.read()` to `to_borsh()`, binding the hash to the chain: [7](#0-6) 

**Solana verification** — `DeployTokenPayload::serialize_for_near()` serializes only the message type prefix and the four metadata fields, with no `SOLANA_OMNI_BRIDGE_CHAIN_ID`:

```rust
fn serialize_for_near(&self, _params: Self::AdditionalParams) -> Result<Vec<u8>> {
    IncomingMessageType::Metadata.serialize(&mut writer)?;
    self.serialize(&mut writer)?;  // token, name, symbol, decimals only
    ...
}
``` [8](#0-7) 

`FinalizeTransferPayload::serialize_for_near()` correctly writes `SOLANA_OMNI_BRIDGE_CHAIN_ID` before both the token address and the recipient address: [9](#0-8) 

### Impact Explanation

The Omni Bridge is deployed on at least 13 chains (`ChainKind` enum: Eth=0, Sol=2, Arb=3, Base=4, Bnb=5, Pol=8, HyperEvm=9, Strk=10, Abs=11, Fogo=12, Aptos=13). [10](#0-9) 

A single NEAR-signed `MetadataPayload` for token `X` is cryptographically valid on every one of these chains simultaneously. An attacker who observes the signature (it is broadcast in a NEAR log event) can call `deployToken` on any chain before the legitimate relayer does. This:

1. **Deploys the bridged token on an unauthorized chain** — the `OmniBridge` contract on that chain sets `isBridgeToken[tokenAddress] = true` and `nearToEthToken[metadata.token] = tokenAddress`, registering the token as a legitimate bridge asset.
2. **Blocks the legitimate relayer** — the subsequent legitimate `deployToken` call reverts with `ERR_TOKEN_EXIST` because `nearToEthToken[metadata.token]` is already set.
3. **Potentially registers unbacked supply** — if the NEAR bridge's relayer indexes `DeployToken` events from all chains (including the replayed one) and registers the token address in `token_id_to_address`, NEAR would sign `finTransfer` messages for that chain. Users could then bridge token X to the unauthorized chain, minting supply that has no corresponding locked backing on NEAR.

### Likelihood Explanation

The NEAR MPC signature is emitted as a public on-chain log event (`LogMetadataEvent`). Any observer can extract it and immediately submit it to any other chain's `deployToken` function. No privileged access, leaked key, or colluding party is required. The attacker only needs to monitor NEAR logs and submit a transaction on the target chain before the relayer does. The Omni Bridge is live on multiple EVM chains with identical contract code, making the replay trivial.

### Recommendation

Include the destination chain identifier in the `MetadataPayload` hash on all chains:

- **NEAR**: Add a `destination_chain: ChainKind` field to `MetadataPayload` and include it in the borsh serialization before signing.
- **EVM**: Append `bytes1(omniBridgeChainId)` to `borshEncoded` in `deployToken()`, mirroring the pattern already used in `finTransfer()`.
- **Starknet**: Change `to_borsh()` to accept `chain_id: u8` and append it, mirroring `TransferMessagePayload.to_borsh(chain_id)`.
- **Solana**: Write `SOLANA_OMNI_BRIDGE_CHAIN_ID` into `serialize_for_near()` for `DeployTokenPayload`, mirroring `FinalizeTransferPayload`.

### Proof of Concept

1. NEAR's relayer calls `log_metadata("usdc.near")` on the NEAR bridge. NEAR MPC signs `keccak256(borsh(Metadata || "usdc.near" || "USD Coin" || "USDC" || 6))` and emits the signature `S` in a `LogMetadataEvent`.
2. The relayer intends to call `deployToken(S, {token:"usdc.near", name:"USD Coin", symbol:"USDC", decimals:6})` on Ethereum.
3. Attacker observes `S` from the NEAR log and calls `deployToken(S, {token:"usdc.near", name:"USD Coin", symbol:"USDC", decimals:6})` on Arbitrum (chain ID 3) first. The EVM `deployToken` hash is `keccak256(borsh(Metadata || "usdc.near" || "USD Coin" || "USDC" || 6))` — identical to `S` — so `ECDSA.recover(hashed, S) == nearBridgeDerivedAddress` passes.
4. USDC bridge token is deployed on Arbitrum. `isBridgeToken[arbUsdcAddr] = true`, `nearToEthToken["usdc.near"] = arbUsdcAddr`.
5. The relayer's subsequent `deployToken` call on Arbitrum reverts with `ERR_TOKEN_EXIST`.
6. If the NEAR bridge's indexer registers `arbUsdcAddr` from the `DeployToken` event, NEAR will sign `finTransfer` messages for Arbitrum USDC, enabling users to bridge USDC to Arbitrum against supply that was only locked for Ethereum.

### Citations

**File:** near/omni-bridge/src/lib.rs (L345-365)
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

        ext_signer::ext(self.mpc_signer.clone())
            .with_static_gas(MPC_SIGNING_GAS)
            .with_attached_deposit(env::attached_deposit())
            .sign(SignRequest {
                payload,
                path: SIGN_PATH.to_owned(),
                key_version: 0,
            })
            .then(
```

**File:** near/omni-types/src/lib.rs (L54-86)
```rust
pub enum ChainKind {
    #[default]
    #[serde(alias = "eth")]
    Eth,
    #[serde(alias = "near")]
    Near,
    #[serde(alias = "sol")]
    Sol,
    #[serde(alias = "arb")]
    Arb,
    #[serde(alias = "base")]
    Base,
    #[serde(alias = "bnb")]
    Bnb,
    #[serde(alias = "btc")]
    Btc,
    #[serde(alias = "zcash")]
    Zcash,
    #[serde(alias = "pol")]
    Pol,
    #[serde(rename = "HlEvm")]
    #[serde(alias = "hlevm")]
    #[strum(serialize = "HlEvm")]
    HyperEvm,
    #[serde(alias = "strk")]
    Strk,
    #[serde(alias = "abs")]
    Abs,
    #[serde(alias = "fogo")]
    Fogo,
    #[serde(alias = "aptos")]
    Aptos,
}
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

**File:** starknet/src/omni_bridge.cairo (L202-205)
```text
        fn deploy_token(ref self: ContractState, signature: Signature, payload: MetadataPayload) {
            assert(!_is_paused(@self, PAUSE_DEPLOY_TOKEN), 'ERR_DEPLOY_TOKEN_PAUSED');

            _verify_borsh_signature(ref self, @payload.to_borsh(), signature);
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

**File:** solana/programs/bridge_token_factory/src/state/message/finalize_transfer.rs (L30-36)
```rust
        writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;
        params.0.serialize(&mut writer)?;
        // 4. amount
        self.amount.serialize(&mut writer)?;
        // 5. recipient
        writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;
        params.1.serialize(&mut writer)?;
```
