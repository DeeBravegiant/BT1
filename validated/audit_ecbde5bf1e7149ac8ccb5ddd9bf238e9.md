### Title
Unbound `payload_hash` in `respond_verify_foreign_tx` Enables Cross-Request Replay of Foreign-Chain Verification Signatures - (File: crates/contract/src/lib.rs)

### Summary
`respond_verify_foreign_tx` verifies only that the submitted `response.payload_hash` carries a valid MPC signature, but never checks that `payload_hash` actually commits to the `ForeignChainRpcRequest` stored in `pending_verify_foreign_tx_requests`. A single Byzantine MPC node (below threshold) can replay a `(payload_hash, signature)` pair from any prior legitimate foreign-tx response to resolve an unrelated pending request, delivering a cryptographically valid but semantically wrong attestation to the waiting caller.

### Finding Description
`respond_verify_foreign_tx` performs the following checks before resolving the pending yield:

1. Caller is an attested participant.
2. Protocol is running.
3. `verify_ecdsa_signature(response.signature, response.payload_hash, root_public_key)` passes. [1](#0-0) 

What is absent is any binding between `response.payload_hash` and the `VerifyForeignTransactionRequest` key used to look up the pending yield. The `payload_hash` is defined as `SHA-256(borsh(ForeignTxSignPayload{request, extracted_values}))`, where `request` encodes the specific `tx_id` and chain parameters. [2](#0-1) 

Because the contract never reconstructs or checks the `ForeignTxSignPayload` embedded in `payload_hash`, any `(payload_hash, signature)` pair that was legitimately produced for request A can be submitted as the response for a completely different pending request B. The contract will accept it, drain the yield queue for B, and return the wrong attestation to B's caller. [3](#0-2) 

The `VerifyForeignTransactionRequest` struct used as the map key contains no entropy or nonce, so the same `tx_id`-bearing request submitted by different callers fans out under one key, but requests with different `tx_id`s are distinct keys. [4](#0-3) 

### Impact Explanation
A bridge contract that calls `verify_foreign_transaction` to attest that a specific foreign-chain transaction finalized before minting tokens or releasing funds receives a `VerifyForeignTransactionResponse{payload_hash, signature}`. If the bridge contract verifies only that the signature is valid over *some* payload_hash (rather than the hash of its own request), the attacker can supply a valid attestation for a fraudulent or non-existent transaction. This enables invalid bridge execution: the bridge accepts a forged proof that a deposit transaction occurred on the foreign chain, triggering an unauthorized token mint or fund release on NEAR.

Even for bridge contracts that do verify the payload_hash binding, the attack still permanently consumes the pending yield for the victim's request (the queue entry is drained), forcing the user to resubmit and pay again, while the attacker can repeat the replay indefinitely.

### Likelihood Explanation
The attack requires exactly one compromised attested MPC participant — strictly below the signing threshold. The `(payload_hash, signature)` material needed for the replay is publicly visible on-chain in any prior `respond_verify_foreign_tx` transaction. The attacker does not need to forge a new MPC signature; they only need to reuse an existing one. The attacker can target any pending `verify_foreign_transaction` request by any user.

### Recommendation
Bind the response's `payload_hash` to the pending request before accepting it. The minimal fix is to require the responder to also supply the `ForeignTxSignPayload` (or at least its `ForeignChainRpcRequest` field), recompute `SHA-256(borsh(payload))` on-chain, and assert it equals `response.payload_hash` and that `payload.request == request.request`. Alternatively, include a per-request nonce (e.g., the NEAR receipt ID already stored in the node-side `VerifyForeignTxRequest`) in the `VerifyForeignTransactionRequest` key so that replaying a response for a different receipt ID fails the map lookup.

### Proof of Concept

**Setup**: Two distinct Bitcoin transactions, `tx_id_A` and `tx_id_B`, both on a supported chain.

1. **Legitimate flow for A**: User submits `verify_foreign_transaction({tx_id: tx_id_A, ...})`. MPC nodes verify and call `respond_verify_foreign_tx(request_A, {payload_hash_A, sig_A})`. The contract accepts it. `payload_hash_A = SHA-256(borsh({request_A, [BlockHash_A]}))` is now public on-chain.

2. **Attack on B**: Attacker (one compromised MPC node) submits `verify_foreign_transaction({tx_id: tx_id_B, ...})` for a fraudulent transaction that never occurred. This creates `pending_verify_foreign_tx_requests[request_B]`.

3. **Replay**: Attacker calls `respond_verify_foreign_tx(request_B, {payload_hash: payload_hash_A, signature: sig_A})`.

4. **Contract evaluation** (lines 718–734):
   - `verify_ecdsa_signature(sig_A, payload_hash_A, root_pk)` → **passes** (valid MPC signature).
   - No check that `payload_hash_A` commits to `request_B`.
   - `resolve_yields_for(&mut pending_verify_foreign_tx_requests, &request_B, serialize({payload_hash_A, sig_A}))` → **yield resolved**.

5. **Result**: The caller of `verify_foreign_transaction(tx_id_B)` receives `{payload_hash_A, sig_A}` — a valid MPC signature attesting to `tx_id_A`'s block hash, delivered as the attestation for `tx_id_B`. A bridge contract that does not re-derive and compare the payload hash will accept this as proof that `tx_id_B` finalized, enabling unauthorized bridge execution. [5](#0-4) [6](#0-5)

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

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L124-128)
```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```
