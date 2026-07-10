### Title
`respond_verify_foreign_tx` accepts any root-key-valid signature regardless of request binding, enabling cross-request replay by a single Byzantine participant - (File: `crates/contract/src/lib.rs`)

### Summary

`respond_verify_foreign_tx` verifies that the submitted signature is valid for `response.payload_hash` under the root public key, but **never verifies that `response.payload_hash` was computed from the `request` argument**. This is the direct analog of the external report's pattern: a restriction is placed on one path (signature validity) while the binding between the signature and the specific pending request is left unenforced. A single Byzantine attested participant can replay any previously-observed valid `(payload_hash, signature)` pair from the public NEAR blockchain to resolve a different pending request with a fabricated foreign-chain attestation.

### Finding Description

In `crates/contract/src/lib.rs`, `respond_verify_foreign_tx` (lines 691–754) performs exactly two security checks:

1. The caller is an attested participant (`assert_caller_is_attested_participant_and_protocol_active`, line 705).
2. The signature is valid for `response.payload_hash` under the domain's root public key (lines 718–734). [1](#0-0) 

It does **not** verify that `response.payload_hash` was derived from the `request` parameter. The canonical payload hash is `SHA-256(borsh(ForeignTxSignPayload::V1 { request, values }))`, which binds the signature to both the specific chain request and the extracted values. [2](#0-1) 

The node-side `build_signature_request` uses `tweak: Tweak::new([0u8; 32])` (zero tweak, root key) for foreign-tx signing, so every `respond_verify_foreign_tx` signature is produced under the same root key with no per-request key derivation. [3](#0-2) 

By contrast, the regular `respond` function derives the expected public key from `request.tweak` (which encodes the caller's account ID and derivation path), cryptographically binding the signature to the specific request. [4](#0-3) 

The `near-mpc-sdk`

### Citations

**File:** crates/contract/src/lib.rs (L596-608)
```rust
                    .as_affine();
                let expected_public_key =
                    derive_key_secp256k1(&affine, &request.tweak).map_err(RespondError::from)?;

                let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");

                // Check the signature is correct
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    payload_hash,
                    &expected_public_key,
                )
                .is_ok()
```

**File:** crates/contract/src/lib.rs (L718-734)
```rust
        let signature_is_valid = match (&response.signature, public_key) {
            (
                dtos::SignatureResponse::Secp256k1(signature_response),
                PublicKeyExtended::Secp256k1 { near_public_key },
            ) => {
                let secp_pk = dtos::Secp256k1PublicKey::try_from(&near_public_key)
                    .expect("Secp256k1 variant always has a secp256k1 key");

                let payload_hash: [u8; 32] = response.payload_hash.0;

                // Check the signature is correct against the root public key
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    &payload_hash,
                    &secp_pk,
                )
                .is_ok()
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L1504-1509)
```rust
impl ForeignTxSignPayload {
    pub fn compute_msg_hash(&self) -> std::io::Result<Hash256> {
        let mut hasher = sha2::Sha256::new();
        borsh::BorshSerialize::serialize(self, &mut hasher)?;
        Ok(Hash256(hasher.finalize().into()))
    }
```

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L39-47)
```rust
    Ok(SignatureRequest {
        id: request.id,
        receipt_id: request.receipt_id,
        payload: Payload::Ecdsa(payload_bytes),
        tweak: Tweak::new([0u8; 32]),
        entropy: request.entropy,
        timestamp_nanosec: request.timestamp_nanosec,
        domain: request.domain_id,
    })
```
