### Title
Missing Payload-Hash-to-Request Binding in `respond_verify_foreign_tx` Allows Cross-Request Signature Replay - (File: crates/contract/src/lib.rs)

### Summary

`respond_verify_foreign_tx` verifies only that the submitted signature is cryptographically valid over the caller-supplied `response.payload_hash`, but never checks that `response.payload_hash` is actually derived from the `request` being resolved. A single Byzantine attested MPC participant can replay a previously-produced valid signature (from any prior `verify_foreign_transaction` signing) as the response to a completely different pending request, causing the contract to resolve that request with fabricated foreign-chain attestation data.

### Finding Description

The `respond_verify_foreign_tx` function in `crates/contract/src/lib.rs` performs the following checks:

1. Caller is an attested participant
2. Protocol is running
3. `accept_requests` is true
4. `verify_ecdsa_signature(signature_response, &payload_hash, &secp_pk)` — the signature is valid over `response.payload_hash` using the root public key [1](#0-0) 

What it does **not** check is that `response.payload_hash` is actually `SHA-256(borsh(ForeignTxSignPayload { request: request.request, values: <observed> }))`. The `payload_hash` is a free parameter supplied by the calling MPC node. [2](#0-1) 

The off-chain SDK verifier (`ForeignChainSignatureVerifier::verify_signature`) does perform this binding check — it recomputes `expected_payload_hash` from `(request, expected_extracted_values)` and asserts `expected_payload_hash == response.payload_hash` before verifying the signature: [3](#0-2) 

The on-chain contract is missing this binding check entirely. The design intent is that `payload_hash` encodes the actual foreign-chain observations for the specific `request`, but the contract never enforces this.

The `ForeignTxSignPayload` structure that defines what `payload_hash` should commit to: [4](#0-3) 

### Impact Explanation

A single Byzantine attested MPC node (the leader) can:

1. Observe that the MPC network previously produced `(payload_hash_A, signature_A)` for `request_A` (e.g., attesting that Bitcoin tx `0xAAA` was included in block `0xBBB`).
2. Wait for a user to submit `verify_foreign_transaction(request_B)` (e.g., for a different Bitcoin tx `0xCCC`).
3. Call `respond_verify_foreign_tx(request_B, { payload_hash: payload_hash_A, signature: signature_A })`.
4. The contract verifies `verify_ecdsa_signature(signature_A, payload_hash_A, root_pk)` → passes (it is a valid prior signature).
5. The contract resolves the pending yield for `request_B` with `{ payload_hash: payload_hash_A, signature: signature_A }`.

The user's contract receives a response that attests to the wrong foreign-chain event. Any downstream bridge or application that does not independently recompute and verify `payload_hash` against the expected `(request_B, observed_values_B)` will accept a forged attestation. This enables invalid bridge execution (e.g., crediting a deposit that never occurred, or crediting the wrong amount/token). [5](#0-4) 

### Likelihood Explanation

The attack requires only a **single** Byzantine attested MPC participant — strictly below the signing threshold. Any node that has participated in at least one prior `verify_foreign_transaction` signing has access to a valid `(payload_hash, signature)` pair it can replay. The attacker does not need to forge a signature or collude with other nodes. The entry path is the public `respond_verify_foreign_tx` contract method, callable by any attested participant.

### Recommendation

In `respond_verify_foreign_tx`, after verifying the signature, recompute the expected payload hash from `request.request` and the `payload_version`, and assert it matches `response.payload_hash`. Concretely, the contract should reconstruct `ForeignTxSignPayload { request: request.request, values: <extracted from response> }` and verify `compute_msg_hash() == response.payload_hash`. Since the full `values` are not stored on-chain (only the hash is returned), the simplest fix is to require the caller to also supply the `ForeignTxSignPayload` (including `values`) and verify both the hash binding and the signature on-chain, mirroring what `ForeignChainSignatureVerifier::verify_signature` does in the SDK. [6](#0-5) 

### Proof of Concept

```
// Setup: MPC network previously signed request_A
// payload_hash_A = SHA-256(borsh(ForeignTxSignPayload{request: request_A, values: [BlockHash(0xBBB)]}))
// signature_A = valid ECDSA signature over payload_hash_A under root key

// Attack:
// 1. User submits verify_foreign_transaction(request_B) — different tx, different expected values
contract.verify_foreign_transaction(request_B_args);
// → pending_verify_foreign_tx_requests[request_B] = [yield_id]

// 2. Byzantine attested node replays old signature for request_A as response to request_B
contract.respond_verify_foreign_tx(
    request_B,                                          // ← correct pending request key
    VerifyForeignTransactionResponse {
        payload_hash: payload_hash_A,                   // ← hash of request_A's data
        signature: signature_A,                         // ← valid signature from prior signing
    }
);
// Contract checks: verify_ecdsa_signature(signature_A, payload_hash_A, root_pk) → OK
// Contract resolves yield for request_B with wrong payload_hash

// 3. User's contract receives {payload_hash: payload_hash_A, signature: signature_A}
//    If user does not call ForeignChainSignatureVerifier::verify_signature,
//    they accept a forged attestation for request_B.
//    If they do call it, their transaction fails and the pending request is permanently consumed (DoS).
``` [7](#0-6)

### Citations

**File:** crates/contract/src/lib.rs (L691-754)
```rust
    #[handle_result]
    pub fn respond_verify_foreign_tx(
        &mut self,
        request: VerifyForeignTransactionRequest,
        response: VerifyForeignTransactionResponse,
    ) -> Result<(), Error> {
        let signer = Self::assert_caller_is_signer();

        log!(
            "respond_verify_foreign_tx: signer={}, request={:?}",
            &signer,
            &request
        );

        self.assert_caller_is_attested_participant_and_protocol_active();

        if !self.protocol_state.is_running_or_resharing() {
            return Err(InvalidState::ProtocolStateNotRunning.into());
        }

        if !self.accept_requests {
            return Err(TeeError::TeeValidationFailed.into());
        }

        let domain = request.domain_id;
        let public_key = self.public_key_extended(domain.0.into())?;

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
            }
            (signature_response, public_key_requested) => {
                return Err(RespondError::SignatureSchemeMismatch {
                    mpc_scheme: Box::new(signature_response.clone()),
                    user_scheme: Box::new(public_key_requested),
                }
                .into());
            }
        };

        if !signature_is_valid {
            return Err(RespondError::InvalidSignature.into());
        }

        pending_requests::resolve_yields_for(
            &mut self.pending_verify_foreign_tx_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
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
