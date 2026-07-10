### Title
`respond_verify_foreign_tx` Does Not Bind `payload_hash` to the Committed Request, Enabling Cross-Request Signature Replay - (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_verify_foreign_tx` verifies only that `response.payload_hash` carries a valid MPC threshold signature, but never checks that `payload_hash` was actually derived from the `request` argument that identifies the pending on-chain yield. A Byzantine attested participant (strictly below the signing threshold) can replay any previously published threshold signature — computed for a completely different foreign-chain transaction — as a valid response to any currently pending `verify_foreign_transaction` request, causing the contract to attest to a foreign-chain event it never verified.

---

### Finding Description

When a user calls `verify_foreign_transaction`, the contract commits to a specific `VerifyForeignTransactionRequest` (containing `domain_id`, `payload_version`, and the chain-specific RPC request such as `tx_id` + `extractors`) and stores a yield keyed on that request. [1](#0-0) 

When an MPC node responds, `respond_verify_foreign_tx` performs two checks:

1. The caller is an attested participant.
2. `response.signature` is a valid ECDSA signature over `response.payload_hash` under the domain's **root** public key. [2](#0-1) 

Critically, **there is no check that `response.payload_hash` was derived from `request`**. The contract never reconstructs `ForeignTxSignPayload { request, values }` and hashes it to compare against `response.payload_hash`.

Contrast this with the regular `respond` function for sign requests, where the payload being verified is taken directly from the committed on-chain request — the contract cannot be given a mismatched payload: [3](#0-2) 

On the node side, `build_signature_request` correctly computes `payload_hash = SHA-256(borsh(ForeignTxSignPayload { request, values }))` and uses it as the MPC signing message: [4](#0-3) 

But the contract never enforces this binding at response time. Once a threshold signature over any `payload_hash` is published on-chain (as part of a legitimately completed `respond_verify_foreign_tx` call), that `(payload_hash, signature)` pair is permanently reusable by any attested participant as a response to any other pending request.

---

### Impact Explanation

**High — Forged foreign-chain verification / invalid bridge execution.**

A Byzantine attested participant can:

1. Observe a legitimately completed `respond_verify_foreign_tx` for request Y (tx_id = Y), obtaining the public `(payload_hash_Y, sig_Y)` pair from the NEAR transaction history.
2. Wait for a new `verify_foreign_transaction` request X (tx_id = X) to appear in the pending map.
3. Call `respond_verify_foreign_tx(request = X, response = { payload_hash = payload_hash_Y, signature = sig_Y })`.
4. The contract accepts: `sig_Y` is a valid threshold signature over `payload_hash_Y` under the root key — the only check performed.
5. The yield for request X is resolved and the caller receives `VerifyForeignTransactionResponse { payload_hash: payload_hash_Y, signature: sig_Y }`.

The caller (e.g., a bridge contract) receives a response that purports to be an MPC attestation of transaction X, but `payload_hash_Y` encodes transaction Y's data. Any downstream contract that trusts the MPC attestation without independently recomputing the hash from the original request will accept a forged proof, enabling invalid bridge execution or double-spend conditions.

---

### Likelihood Explanation

**Medium.** The attack requires only a single Byzantine attested participant — no threshold collusion. The necessary `(payload_hash, signature)` material is fully public on-chain after any legitimate `respond_verify_foreign_tx` call. In a live bridge deployment with continuous traffic, valid signatures accumulate rapidly, giving an attacker ample replay material. The attacker must act within the yield-resume timeout window (~200 blocks per `REQUEST_EXPIRATION_BLOCKS`), which is a practical but not prohibitive constraint. [5](#0-4) 

---

### Recommendation

In `respond_verify_foreign_tx`, reconstruct the expected `payload_hash` from the committed `request` and the `values` supplied in the response, then assert equality before accepting the signature:

```rust
// Reconstruct the canonical payload from the committed request + reported values
let expected_payload = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
    request: request.request.clone(),
    values: response.values.clone(), // values must be added to the response DTO
});
let expected_hash = expected_payload.compute_msg_hash()
    .map_err(|_| RespondError::InvalidPayloadHash)?;

require!(
    expected_hash == response.payload_hash,
    RespondError::PayloadHashMismatch
);
```

Alternatively, mirror the design of `respond`: store the expected `payload_hash` in the pending-request map at submission time (once nodes agree on the extracted values) and compare against it at response time. This is the same pattern used by `respond`, where `request.payload` is the authoritative message and the contract never accepts a caller-supplied payload.

---

### Proof of Concept

1. Deploy the contract with a ForeignTx domain and register Bitcoin as a supported chain.
2. Submit `verify_foreign_transaction` for `tx_id = [0xAA; 32]` (request A). Let it complete legitimately — the on-chain receipt contains `payload_hash_A` and `sig_A`.
3. Submit a second `verify_foreign_transaction` for `tx_id = [0xBB; 32]` (request B). It is now pending.
4. As any attested participant, call:
   ```json
   respond_verify_foreign_tx(
     request = { domain_id: 0, payload_version: V1, request: Bitcoin { tx_id: [0xBB;32], ... } },
     response = { payload_hash: payload_hash_A, signature: sig_A }
   )
   ```
5. The contract accepts the call (signature is valid over `payload_hash_A` under the root key).
6. Request B's yield resolves and returns `payload_hash_A` — the attestation for transaction A — to the caller of request B.
7. Any bridge contract that trusts this response without recomputing the hash from `tx_id = [0xBB;32]` will incorrectly conclude that transaction `0xBB` was verified by the MPC network. [6](#0-5) [4](#0-3)

### Citations

**File:** crates/contract/src/lib.rs (L549-556)
```rust
        let request = args_into_verify_foreign_tx_request(request);
        let callback_args = serde_json::to_vec(&(&request,)).unwrap();
        self.enqueue_yield_request(
            method_names::RETURN_VERIFY_FOREIGN_TX_AND_CLEAN_STATE_ON_SUCCESS,
            callback_args,
            callback_gas,
            move |this, id| this.add_verify_foreign_tx_request(request, id),
        );
```

**File:** crates/contract/src/lib.rs (L600-608)
```rust
                let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");

                // Check the signature is correct
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    payload_hash,
                    &expected_public_key,
                )
                .is_ok()
```

**File:** crates/contract/src/lib.rs (L691-753)
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

**File:** crates/node/src/requests/queue.rs (L32-33)
```rust
/// This is equal to the yield-resume timeout on the blockchain.
pub const REQUEST_EXPIRATION_BLOCKS: NumBlocks = 200;
```
