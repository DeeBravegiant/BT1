### Title
Missing Tweak Derivation in `verify_foreign_transaction` Causes All Callers to Share Root ForeignTx Key — (`crates/contract/src/dto_mapping.rs`)

### Summary
The design specification for `verify_foreign_transaction` requires per-caller key derivation via a `derivation_path`→`tweak` conversion, analogous to how `sign()` derives a per-caller key. The conversion function `args_into_verify_foreign_tx_request` silently drops this derivation entirely — no `derivation_path` field exists in the live `VerifyForeignTransactionRequestArgs`, no `tweak` field exists in `VerifyForeignTransactionRequest`, and `respond_verify_foreign_tx` verifies signatures against the bare root ForeignTx public key. Every caller therefore receives an attestation signed under the same root key, breaking the per-caller key-isolation invariant the design mandates.

### Finding Description
The design document (`docs/foreign-chain-transactions.md`) explicitly specifies the intended data flow:

```rust
// Intended design (from docs/foreign-chain-transactions.md lines 98-110)
pub struct VerifyForeignTransactionRequestArgs {
    pub request: ForeignChainRpcRequest,
    pub derivation_path: String,   // <-- key derivation path
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}

pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub tweak: Tweak,              // <-- derived from derivation_path
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```

The design further specifies a foreign-tx-specific tweak prefix to prevent cross-purpose key reuse:

```rust
// docs/foreign-chain-transactions.md lines 269-282
const FOREIGN_TX_TWEAK_DERIVATION_PREFIX: &str =
    "near-mpc-recovery v0.1.0 foreign-tx epsilon derivation:";
pub fn derive_foreign_tx_tweak(predecessor_id: &AccountId, path: &str) -> Tweak { ... }
```

**Actual implementation** — the live structs have neither field: [1](#0-0) 

The conversion function simply copies three fields and derives nothing: [2](#0-1) 

Consequently, `respond_verify_foreign_tx` verifies the MPC-produced signature against the **root** ForeignTx public key — no tweak is applied, unlike `respond` which always derives the expected key from `request.tweak`: [3](#0-2) 

Compare with the tweak-aware path in `respond`: [4](#0-3) 

The unit test even documents this as intentional for the current state: [5](#0-4) 

### Impact Explanation
All `verify_foreign_transaction` callers — regardless of their account ID or derivation path — receive a signature produced under the same root ForeignTx key. This breaks the production safety invariant the design mandates:

1. **No per-caller key isolation.** A bridge contract that calls `derived_public_key(path, predecessor)` to obtain the expected verification key for a specific caller will receive a key that does not match the root ForeignTx key used to produce the signature. Verification fails silently, permanently blocking the bridge flow for that caller.
2. **Cross-caller attestation replay.** Because the signed payload is bound to `(ForeignChainRpcRequest, extracted_values)` but not to a caller identity, any party that obtains a valid `VerifyForeignTransactionResponse` for a given transaction can present it as their own attestation to a bridge contract that verifies only against the root ForeignTx key. This enables unauthorized bridge execution for transactions the attacker did not initiate.
3. **Domain-separation gap.** The design explicitly requires a distinct tweak prefix for ForeignTx vs. Sign to prevent cross-purpose key reuse. Without the tweak, the ForeignTx domain's root key is exposed as a flat, caller-agnostic signing oracle for any whitelisted foreign transaction, widening the attack surface for forged bridge attestations.

This matches the **Medium** allowed impact: contract execution-flow manipulation that breaks production safety/accounting invariants (per-caller key isolation) without requiring operator misconfiguration.

### Likelihood Explanation
Any bridge contract built against the documented API — which specifies `derivation_path` and per-caller derived keys — will be broken or exploitable. The entry path requires only an unprivileged NEAR account submitting a `verify_foreign_transaction` call with a 1 yoctoNEAR deposit; no privileged access is needed.

### Recommendation
1. Add `derivation_path: String` to `VerifyForeignTransactionRequestArgs` and `tweak: Tweak` to `VerifyForeignTransactionRequest` as the design document specifies.
2. Implement `args_into_verify_foreign_tx_request` to call `derive_foreign_tx_tweak(predecessor_id, &args.derivation_path)` and populate the `tweak` field.
3. Update `respond_verify_foreign_tx` to derive the expected public key using `derive_key_secp256k1(&affine, &request.tweak)` before calling `verify_ecdsa_signature`, mirroring the pattern in `respond`.
4. Add unit tests for `verify_foreign_transaction` that assert the signature is rejected when verified against the root key (

### Citations

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L101-128)
```rust
pub struct VerifyForeignTransactionRequestArgs {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}

#[derive(
    Debug,
    Clone,
    Eq,
    PartialEq,
    Ord,
    PartialOrd,
    Hash,
    Serialize,
    Deserialize,
    BorshSerialize,
    BorshDeserialize,
)]
#[cfg_attr(
    all(feature = "abi", not(target_arch = "wasm32")),
    derive(schemars::JsonSchema, borsh::BorshSchema)
)]
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```

**File:** crates/contract/src/dto_mapping.rs (L840-848)
```rust
pub fn args_into_verify_foreign_tx_request(
    args: dtos::VerifyForeignTransactionRequestArgs,
) -> dtos::VerifyForeignTransactionRequest {
    dtos::VerifyForeignTransactionRequest {
        domain_id: args.domain_id,
        request: args.request,
        payload_version: args.payload_version,
    }
}
```

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

**File:** crates/contract/src/lib.rs (L3693-3698)
```rust
        let payload_hash = payload.compute_msg_hash().unwrap().0;
        // simulate signature with the root key (no tweak for foreign tx)
        let secret_key_ec: elliptic_curve::SecretKey<Secp256k1> =
            elliptic_curve::SecretKey::from_bytes(&secret_key.to_bytes()).unwrap();
        let secret_key = SigningKey::from_bytes(&secret_key_ec.to_bytes()).unwrap();
        let (signature, recovery_id) = secret_key.sign_prehash_recoverable(&payload_hash).unwrap();
```
