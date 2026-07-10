### Title
`respond_verify_foreign_tx` Accepts Caller-Supplied `payload_hash` Without Binding It to the Pending Request — (File: `crates/contract/src/lib.rs`)

### Summary

The `respond_verify_foreign_tx` function verifies that the submitted signature is valid for the caller-supplied `response.payload_hash` under the domain's root public key, but never checks that `response.payload_hash` is the correct hash for the pending `request`. A single Byzantine participant (below the signing threshold) who has previously obtained a legitimately-produced MPC signature for any `ForeignTxSignPayload` can replay that `(payload_hash, signature)` pair against any other pending `verify_foreign_transaction` request, causing the caller to receive a `VerifyForeignTransactionResponse` that attests to the wrong foreign-chain data.

### Finding Description

In `crates/contract/src/lib.rs` lines 718–734, `respond_verify_foreign_tx` performs the following check:

```rust
let payload_hash: [u8; 32] = response.payload_hash.0;   // taken from the response, not derived

near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,                                            // root key of the domain
)
.is_ok()
``` [1](#0-0) 

The contract then resolves the pending yield for `request` with the full `response`:

```rust
pending_requests::resolve_yields_for(
    &mut self.pending_verify_foreign_tx_requests,
    &request,
    serde_json::to_vec(&response).unwrap(),
)
``` [2](#0-1) 

The two checks performed are:
1. `request` exists in the pending map.
2. `response.signature` is a valid ECDSA signature over `response.payload_hash` under the domain root key.

What is **never** checked: that `response.payload_hash` equals `ForeignTxSignPayload::V1 { request: request.request, values: <observed values> }.compute_msg_hash()`.

The canonical payload is defined as:

```rust
pub struct ForeignTxSignPayloadV1 {
    pub request: ForeignChainRpcRequest,   // includes tx_id, extractors, finality
    pub values: Vec<ExtractedValue>,       // values observed on the foreign chain
}
``` [3](#0-2) 

Crucially, `ForeignTxSignPayload` contains **no** domain ID, no NEAR contract address, and no per-request nonce. The node-side signing path confirms the zero-tweak (root key) usage:

```rust
Ok(SignatureRequest {
    ...
    tweak: Tweak::new([0u8; 32]),   // root key, no derivation
    domain: request.domain_id,
})
``` [4](#0-3) 

Because the contract never re-derives the expected `payload_hash` from the pending `request`, any `(payload_hash_X, sig_X)` pair that is a valid root-key signature — regardless of which foreign transaction it actually attests to — will be accepted as a response to any pending request on the same domain.

### Impact Explanation

A Byzantine participant (strictly below the signing threshold) who has previously received a legitimately-produced `VerifyForeignTransactionResponse` for **any** prior request on the same domain can:

1. Let victim submit `verify_foreign_transaction(request_B)` (e.g., verify Bitcoin tx `B`).
2. Call `respond_verify_foreign_tx(request_B, { payload_hash: payload_hash_A, sig_A })` — where `(payload_hash_A, sig_A)` was produced by the MPC network for a completely different transaction `A`.
3. The contract accepts the call: `sig_A` is a valid root-key signature over `payload_hash_A`, and `request_B` is pending.
4. The victim's promise resolves with `{ payload_hash: payload_hash_A, sig_A }` — an MPC-signed attestation for transaction `A`'s data, not `B`'s.

Any on-chain consumer that does not independently reconstruct and compare the expected `payload_hash` (as the SDK's `ForeignChainSignatureVerifier::verify_signature` does) will accept this as proof that transaction `B` was verified, enabling forged foreign-chain verification. This breaks the core safety invariant of the `verify_foreign_transaction` flow: that the returned signature attests to the specific transaction the caller requested. [5](#0-4) 

This maps to the **Medium** allowed impact: *"request-lifecycle or contract execution-flow manipulation that breaks production safety/accounting invariants."*

### Likelihood Explanation

- A single Byzantine participant (one compromised or malicious TEE node, below threshold) is sufficient — no threshold collusion is required.
- The attacker does not need to forge any cryptographic material; they only need to retain a `VerifyForeignTransactionResponse` from any prior legitimate signing session on the same domain.
- The MPC network regularly produces such responses in normal operation, so the attacker accumulates usable `(payload_hash, sig)` pairs over time.
- The attack is executable in a single `respond_verify_foreign_tx` call with no observable precursor on-chain.

### Recommendation

The contract must bind `response.payload_hash` to the pending `request` before accepting the response. Since the contract does not observe the extracted `values`, the binding must be enforced structurally:

1. **Include a per-request unique identifier in `ForeignTxSignPayload`** — e.g., the `receipt_id` or the on-chain `SignatureId` — so that the signed hash is cryptographically tied to exactly one pending request. The contract can then re-derive the expected prefix and reject any response whose `payload_hash` does not embed the correct identifier.

2. **Alternatively, have the node pass the full `ForeignTxSignPayload` (not just its hash) in the response**, and have the contract verify `borsh_hash(payload) == response.payload_hash` and `payload.request == request.request`. This lets the contract confirm the hash corresponds to the correct `ForeignChainRpcRequest` without knowing the extracted values.

### Proof of Concept

```
// Setup: Byzantine node B has previously obtained a valid response for tx_A.
let (payload_hash_A, sig_A) = previously_obtained_mpc_response_for_tx_A;

// Victim submits a new request for tx_B.
contract.verify_foreign_transaction(request_args_B);  // pending: request_B

// Byzantine node B calls respond with tx_A's payload_hash and signature.
// The contract only checks: sig_A valid for payload_hash_A under root key? YES.
// The contract does NOT check: payload_hash_A == hash(ForeignTxSignPayload { request_B.request, ... })
contract.respond_verify_foreign_tx(
    request_B,
    VerifyForeignTransactionResponse {
        payload_hash: payload_hash_A,   // hash of tx_A's data
        signature: sig_A,               // valid root-key sig over payload_hash_A
    }
);

// Victim's promise resolves with tx_A's attestation, not tx_B's.
// Any consumer that skips payload_hash verification is misled.
```

The root cause is at: [6](#0-5)

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
