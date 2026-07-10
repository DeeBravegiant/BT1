### Title
`ForeignTxSignPayloadV1` Lacks Contract Address and Chain-ID Domain Separator, Enabling Cross-Deployment Signature Replay - (File: `crates/near-mpc-contract-interface/src/types/foreign_chain.rs`)

### Summary

The MPC network's `verify_foreign_transaction` flow signs a payload whose hash is computed exclusively from the foreign-chain request data and extracted values. No NEAR contract address, NEAR network/chain identifier, or domain ID is included in the signed material. A `VerifyForeignTransactionResponse` produced by one MPC contract deployment is therefore cryptographically indistinguishable from one produced by any other deployment that shares the same key, enabling cross-deployment and cross-network replay.

### Finding Description

`ForeignTxSignPayloadV1` is defined as:

```rust
pub struct ForeignTxSignPayloadV1 {
    pub request: ForeignChainRpcRequest,
    pub values: Vec<ExtractedValue>,
}
``` [1](#0-0) 

The message hash that MPC nodes actually sign is:

```
msg_hash = SHA-256(borsh(ForeignTxSignPayload))
``` [2](#0-1) 

This hash contains **only** the foreign-chain request and the extracted values. It does not contain:

- The NEAR contract account ID (`address(this)` equivalent)
- The NEAR network identifier (mainnet / testnet)
- The `domain_id` that selects the signing key

The `domain_id` is present in `VerifyForeignTransactionRequest` and is used to look up the signing key inside `respond_verify_foreign_tx`, but it is never fed into the payload that is actually signed:

```rust
fn build_signature_request(
    request: &VerifyForeignTxRequest,
    foreign_tx_payload: &dtos::ForeignTxSignPayload,
) -> anyhow::Result<SignatureRequest> {
    let payload_hash = foreign_tx_payload.compute_msg_hash()?.into();
    // domain_id is passed to select the key but is NOT part of the hash
    Ok(SignatureRequest {
        tweak: Tweak::new([0u8; 32]),   // zero tweak – no key derivation
        domain: request.domain_id,
        ...
    })
}
``` [3](#0-2) 

The downstream SDK verifier (`ForeignChainSignatureVerifier::verify_signature`) reconstructs the expected payload from `(request, expected_extracted_values)` and checks the signature against a caller-supplied public key. It has no mechanism to bind the signature to a specific MPC contract deployment or NEAR network:

```rust
let expected_payload = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
    request: self.request,
    values: self.expected_extracted_values,
});
let expected_payload_hash = expected_payload.compute_msg_hash()...;
// No contract address, no chain ID, no domain ID checked
``` [4](#0-3) 

### Impact Explanation

A `VerifyForeignTransactionResponse` signature is valid for **any** NEAR contract that (a) holds the same MPC public key and (b) accepts the same `(request, values)` tuple. Concrete scenarios:

1. **Testnet → Mainnet replay**: If the same key material is used on both NEAR testnet and mainnet (e.g., during a staged rollout or migration), a signature obtained cheaply on testnet for a foreign-chain transaction can be replayed on mainnet. A downstream bridge contract on mainnet would accept it as proof that the foreign transaction was verified by the mainnet MPC network.

2. **Contract migration / upgrade**: When the MPC contract is redeployed to a new account ID but the key shares are migrated, all signatures issued by the old contract remain valid on the new contract. An attacker who captured a response before the migration can replay it after.

3. **Cross-domain replay within the same contract**: Because `domain_id` is absent from the signed payload, a signature produced under domain A is valid under domain B if both domains expose the same public key (e.g., during a domain reconfiguration).

The impact is forged foreign-chain verification: a downstream bridge contract is deceived into believing a foreign transaction was attested by the correct MPC deployment when it was attested by a different one, enabling invalid bridge execution or double-spend conditions.

### Likelihood Explanation

The attack requires the attacker to obtain a valid `VerifyForeignTransactionResponse` from one deployment and submit it to another deployment that shares the same key. Key-sharing across deployments is a realistic operational condition (testnet/mainnet parity during rollout, contract migration). The attacker needs no privileged access — `verify_foreign_transaction` is a public, payable entry point callable by any NEAR account. [5](#0-4) 

### Recommendation

Include a domain separator in `ForeignTxSignPayloadV1` (or in a new `V2` variant) that commits to:

1. The NEAR contract account ID (`env::current_account_id()` at request time, stored in the payload).
2. The NEAR chain/network ID.
3. The `domain_id` used to select the signing key.

For example:

```rust
pub struct ForeignTxSignPayloadV2 {
    pub contract_id: AccountId,   // env::current_account_id()
    pub near_chain_id: u64,       // env::block_height() chain discriminator or a static constant
    pub domain_id: DomainId,
    pub request: ForeignChainRpcRequest,
    pub values: Vec<ExtractedValue>,
}
```

The `ForeignChainSignatureVerifier` in the SDK must be updated to reconstruct and verify the same domain-separated payload.

### Proof of Concept

1. Deploy MPC contract A on NEAR testnet; complete DKG to obtain key K.
2. Deploy MPC contract B on NEAR mainnet with the same key K (migration scenario).
3. On testnet, call `verify_foreign_transaction` for Bitcoin tx T with extractor `BlockHash`. Receive a valid `VerifyForeignTransactionResponse { payload_hash, signature }`.
4. On mainnet, submit the identical `VerifyForeignTransactionResponse` to a downstream bridge contract that calls `ForeignChainSignatureVerifier::verify_signature(response, K_mainnet_public_key)`.
5. Because `ForeignTxSignPayloadV1` contains only `(request, values)` — identical on both networks — the signature verifies successfully. The bridge contract accepts the testnet-attested response as a valid mainnet attestation.

The root cause is confirmed at: [6](#0-5) [3](#0-2) [7](#0-6)

### Citations

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L1499-1509)
```rust
pub struct ForeignTxSignPayloadV1 {
    pub request: ForeignChainRpcRequest,
    pub values: Vec<ExtractedValue>,
}

impl ForeignTxSignPayload {
    pub fn compute_msg_hash(&self) -> std::io::Result<Hash256> {
        let mut hasher = sha2::Sha256::new();
        borsh::BorshSerialize::serialize(self, &mut hasher)?;
        Ok(Hash256(hasher.finalize().into()))
    }
```

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L30-48)
```rust
fn build_signature_request(
    request: &VerifyForeignTxRequest,
    foreign_tx_payload: &dtos::ForeignTxSignPayload,
) -> anyhow::Result<SignatureRequest> {
    let payload_hash: [u8; ECDSA_PAYLOAD_SIZE_BYTES] =
        foreign_tx_payload.compute_msg_hash()?.into();
    let payload_bytes: BoundedVec<u8, ECDSA_PAYLOAD_SIZE_BYTES, ECDSA_PAYLOAD_SIZE_BYTES> =
        payload_hash.into();

    Ok(SignatureRequest {
        id: request.id,
        receipt_id: request.receipt_id,
        payload: Payload::Ecdsa(payload_bytes),
        tweak: Tweak::new([0u8; 32]),
        entropy: request.entropy,
        timestamp_nanosec: request.timestamp_nanosec,
        domain: request.domain_id,
    })
}
```

**File:** crates/near-mpc-sdk/src/foreign_chain.rs (L41-89)
```rust
impl ForeignChainSignatureVerifier {
    pub fn verify_signature(
        self,
        response: &VerifyForeignTransactionResponse,
        // TODO(#2232): don't use interface API types for public keys
        public_key: &PublicKey,
    ) -> Result<(), VerifyForeignChainError> {
        let expected_payload = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
            request: self.request,
            values: self.expected_extracted_values,
        });

        let expected_payload_hash = expected_payload
            .compute_msg_hash()
            .map_err(|_| VerifyForeignChainError::FailedToComputeMsgHash)?;

        let payload_is_correct = expected_payload_hash == response.payload_hash;

        if !payload_is_correct {
            return Err(VerifyForeignChainError::IncorrectPayloadSigned {
                got: response.payload_hash.clone(),
                expected: expected_payload_hash,
            });
        }
        let verification_result = match (public_key, &response.signature) {
            (
                PublicKey::Secp256k1(secp256k1_public_key),
                SignatureResponse::Secp256k1(k256_signature),
            ) => near_mpc_signature_verifier::verify_ecdsa_signature(
                k256_signature,
                &expected_payload_hash,
                secp256k1_public_key,
            ),
            (PublicKey::Ed25519(ed25519_public_key), SignatureResponse::Ed25519 { signature }) => {
                near_mpc_signature_verifier::verify_eddsa_signature(
                    signature,
                    expected_payload_hash.as_slice(),
                    ed25519_public_key,
                )
            }
            // TODO(#2234): improve types so these errors can't happen
            (PublicKey::Bls12381(_bls12381_g2_public_key), _) => {
                return Err(VerifyForeignChainError::UnexpectedSignatureScheme);
            }
            _ => return Err(VerifyForeignChainError::UnexpectedSignatureScheme),
        };

        verification_result.map_err(|_| VerifyForeignChainError::SignatureVerificationFailed)
    }
```

**File:** crates/contract/src/lib.rs (L518-519)
```rust
    #[payable]
    pub fn verify_foreign_transaction(&mut self, request: VerifyForeignTransactionRequestArgs) {
```
