### Title
`respond_verify_foreign_tx` Accepts Arbitrary `payload_hash` Without Binding It to the Submitted `request` — (File: crates/contract/src/lib.rs)

### Summary

The `respond_verify_foreign_tx` contract method verifies only that the supplied `response.signature` is a valid ECDSA signature over `response.payload_hash` under the domain's root public key. It never checks that `payload_hash` was actually derived from the `request` argument that identifies which pending foreign-chain verification is being resolved. A single Byzantine MPC participant (strictly below the signing threshold) can therefore replay any previously observed, legitimately-produced threshold signature — paired with its original `payload_hash` — against a *different* pending `VerifyForeignTransactionRequest`, causing the contract to resolve that request with a fabricated observation.

### Finding Description

In `respond_verify_foreign_tx` (lines 691–754 of `crates/contract/src/lib.rs`), the signature check is:

```rust
let payload_hash: [u8; 32] = response.payload_hash.0;

// Check the signature is correct against the root public key
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,
)
.is_ok()
```

`payload_hash` is taken directly from the caller-supplied `response`, not recomputed from `request`. The contract never asserts:

```
payload_hash == SHA-256(borsh(ForeignTxSignPayload { request: request.request, values: <observed> }))
```

Compare this with the analogous `respond` function for plain signing (lines 597–608), which derives the expected public key from `request.tweak` and verifies the signature against the payload stored *inside* `request.payload` — both of which are bound to the original user submission. `respond_verify_foreign_tx` has no equivalent binding.

The `build_signature_request` helper on the node side (lines 30–47 of `crates/node/src/providers/verify_foreign_tx/sign.rs`) uses a zero tweak (`Tweak::new([0u8; 32])`), so the root key signs the `payload_hash`. Once any such signature exists on-chain (visible to all participants), a Byzantine node can extract `(payload_hash_A, signature_A)` from a completed response for request A and submit:

```
respond_verify_foreign_tx(request_B, { payload_hash: payload_hash_A, signature: signature_A })
```

The contract will:
1. Confirm `request_B` is pending — passes.
2. Verify `signature_A` over `payload_hash_A` under the root key — passes (it is a genuine threshold signature).
3. Drain all queued yields for `request_B`, delivering `{ payload_hash_A, signature_A }` to every waiting caller.

No threshold collusion is required; the attacker only replays an already-produced signature.

### Impact Explanation

Every caller waiting on `request_B` (e.g., a bridge contract verifying that Bitcoin tx Y finalized) receives a `VerifyForeignTransactionResponse` whose `payload_hash` encodes the observation for a *different* transaction (tx X). The contract marks `request_B` as resolved and removes it from `pending_verify_foreign_tx_requests`. Any downstream contract that does not independently recompute and compare the expected hash — using `ForeignChainSignatureVerifier` from the SDK — will treat the forged attestation as genuine, potentially triggering an invalid bridge payout or double-spend.

This matches the allowed High impact: **forged foreign-chain verification that causes invalid bridge execution**.

### Likelihood Explanation

A single attested MPC participant (Byzantine, below threshold) can execute this attack:
- They observe a completed `respond_verify_foreign_tx` call on-chain (all NEAR transactions are public).
- They extract `(request_A, payload_hash_A, signature_A)`.
- They wait for any other pending `request_B` to appear in `pending_verify_foreign_tx_requests`.
- They call `respond_verify_foreign_tx(request_B, { payload_hash_A, signature_A })` before the honest leader does.

No key material needs to be compromised; no threshold cooperation is needed. The window of opportunity exists whenever two different foreign-tx requests are pending concurrently, which is the normal operating condition for a bridge.

### Recommendation

Recompute the expected `payload_hash` from `request` inside `respond_verify_foreign_tx` and assert equality before accepting the response:

```rust
// Inside respond_verify_foreign_tx, after extracting secp_pk:
let expected_payload = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
    request: request.request.clone(),
    // values cannot be recomputed here; instead, verify the hash
    // is consistent with the request by requiring nodes to include
    // the full payload in the response, or bind payload_hash to
    // the request key at enqueue time.
    ..
});
```

The cleanest fix is to have the contract recompute `payload_hash` from the `request` fields it already holds (chain, tx_id, extractors, payload_version) and the `values` included in the response, then assert `computed_hash == response.payload_hash` before verifying the signature. Alternatively, bind the expected hash to the pending-request map entry at enqueue time so the respond path can compare against it directly.

### Proof of Concept

**Setup:** Two pending requests exist simultaneously:
- `request_A`: Bitcoin tx `[0xAA; 32]`, extractor `BlockHash`
- `request_B`: Bitcoin tx `[0xBB; 32]`, extractor `BlockHash`

**Step 1 — Honest resolution of request A:**
The MPC network produces `(payload_hash_A, signature_A)` and the leader calls:
```
respond_verify_foreign_tx(request_A, { payload_hash: payload_hash_A, signature: signature_A })
```
This is accepted; callers of `request_A` receive the correct response.

**Step 2 — Byzantine replay against request B:**
The Byzantine participant (one node, below threshold) calls:
```
respond_verify_foreign_tx(request_B, { payload_hash: payload_hash_A, signature: signature_A })
```

The contract at lines 718–734 checks only:
```rust
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,   // signature_A — valid
    &payload_hash,        // payload_hash_A — valid hash for request_A, not request_B
    &secp_pk,             // root public key
)
.is_ok()                  // → true
```

`request_B` is resolved; its callers receive `{ payload_hash_A, signature_A }`. A bridge contract that does not call `ForeignChainSignatureVerifier::verify_signature` will accept this as proof that Bitcoin tx `[0xBB; 32]` finalized, when in fact only tx `[0xAA; 32]` was ever verified. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L30-47)
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

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L124-128)
```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```
