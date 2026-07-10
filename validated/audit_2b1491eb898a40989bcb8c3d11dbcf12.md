### Title
`respond_verify_foreign_tx` Accepts Caller-Supplied `payload_hash` Without Binding It to the Stored Request - (File: `crates/contract/src/lib.rs`)

### Summary

The `respond_verify_foreign_tx` contract method verifies that the submitted ECDSA signature is valid over the caller-supplied `response.payload_hash`, but never checks that `payload_hash` is actually derived from the stored `VerifyForeignTransactionRequest`. A single Byzantine attested participant (strictly below the signing threshold) can reuse a legitimately produced MPC signature from one resolved request to resolve a different, still-pending request, delivering a forged foreign-chain verification response to the waiting caller.

### Finding Description

`respond_verify_foreign_tx` performs exactly two semantic checks before resolving the pending yield:

1. The caller is an attested participant.
2. `response.signature` is a valid ECDSA signature over `response.payload_hash` under the root public key. [1](#0-0) 

What it does **not** check is that `response.payload_hash` is the canonical hash of a `ForeignTxSignPayload` whose embedded `ForeignChainRpcRequest` matches the `request` argument that was used to look up the pending yield. The `payload_hash` is defined as:

```
payload_hash = SHA-256(borsh(ForeignTxSignPayload { request: ForeignChainRpcRequest, values: Vec<ExtractedValue> }))
``` [2](#0-1) 

Because `values` (the extracted foreign-chain observations) are never stored on-chain, the contract has no way to recompute the hash from the stored request alone. It therefore accepts any `payload_hash` for which a valid root-key signature exists, regardless of which `ForeignChainRpcRequest` that hash actually encodes.

Contrast this with the regular `respond` path, where the payload is part of the stored `SignatureRequest` and the contract verifies the signature against the known, on-chain payload: [3](#0-2) 

The `pending_verify_foreign_tx_requests` map is keyed on the full `VerifyForeignTransactionRequest`: [4](#0-3) 

After the signature check passes, the contract resolves all queued yields for `request` with the full `response` (including the unverified `payload_hash`): [5](#0-4) 

The yield callback simply returns the response verbatim to the original caller: [6](#0-5) 

### Impact Explanation

A Byzantine attested participant can resolve a pending `verify_foreign_transaction` request for transaction `tx_id=X` with a response whose `payload_hash` encodes a completely different transaction `tx_id=Y`. The user's contract receives a `VerifyForeignTransactionResponse` that carries a valid MPC signature, but the signed payload does not correspond to the transaction the user requested verification for. Any bridge or application contract that does not independently recompute and compare the `payload_hash` (as the SDK helper does) will accept this forged attestation as proof that `tx_id=X` was verified, enabling invalid bridge execution or double-spend conditions.

This matches the **High** allowed impact: *"Cross-chain replay, forged foreign-chain verification, light-client-style verification bypass, or participant/attestation authorization bypass that causes invalid bridge execution or double-spend conditions."*

### Likelihood Explanation

The attack requires only:

1. **One Byzantine attested participant** (strictly below threshold) — they cannot forge a signature, but they can reuse any signature already published on-chain.
2. **Two concurrently pending requests** — one whose response has already been published (`request_B`), and one still pending (`request_A`).

Both conditions are routinely satisfied in production: NEAR is a public chain, so every `respond_verify_foreign_tx` call (including its `response` argument) is visible to all participants. A single compromised or malicious node can observe a valid `(request_B, response_B)` pair and immediately submit `respond_verify_foreign_tx(request_A, response_B)` for any other pending request.

### Recommendation

The contract must bind the accepted `payload_hash` to the specific stored request. Two viable approaches:

1. **Include the raw `ForeignChainRpcRequest` in the response** so the contract can verify it matches the stored request before accepting the `payload_hash`.
2. **Change the signing payload** to include a domain separator derived from the full `VerifyForeignTransactionRequest` key (e.g., sign `SHA-256(borsh(request_key) || SHA-256(borsh(ForeignTxSignPayload)))`), so a signature produced for `request_B` is cryptographically invalid for `request_A`.

The SDK-side verifier already performs the correct check — it recomputes the expected hash and compares it to `response.payload_hash`: [7](#0-6) 

This logic must be enforced in the contract itself, not left as an optional client-side step.

### Proof of Concept

1. User A calls `verify_foreign_transaction(request_A)` where `request_A.request = BitcoinRpcRequest { tx_id: X, ... }`. The contract queues a yield under key `request_A`.
2. User B calls `verify_foreign_transaction(request_B)` where `request_B.request = BitcoinRpcRequest { tx_id: Y, ... }`. The contract queues a yield under key `request_B`.
3. The MPC network legitimately processes `request_B` and an honest node calls `respond_verify_foreign_tx(request_B, response_B)` where `response_B.payload_hash = SHA-256({request_B.request, observed_values_B})`. This is accepted and published on-chain.
4. A Byzantine attested participant observes `response_B` on the public NEAR blockchain. `request_A` is still pending.
5. The Byzantine participant calls `respond_verify_foreign_tx(request_A, response_B)`.
6. The contract checks: caller is attested ✓; `response_B.signature` is valid over `response_B.payload_hash` under the root key ✓; `request_A` is in `pending_verify_foreign_tx_requests` ✓.
7. The contract resolves all yields for `request_A` with `response_B`.
8. User A's contract receives `response_B` — a valid MPC signature, but over a `payload_hash` that encodes `tx_id=Y`, not `tx_id=X`.
9. Any bridge contract that does not call `ForeignChainSignatureVerifier::verify_signature` will treat this as proof that `tx_id=X` was verified on Bitcoin, enabling fraudulent bridge execution. [8](#0-7)

### Citations

**File:** crates/contract/src/lib.rs (L153-156)
```rust
    pending_signature_requests: LookupMap<SignatureRequest, Vec<YieldIndex>>,
    pending_ckd_requests: LookupMap<CKDRequest, Vec<YieldIndex>>,
    pending_verify_foreign_tx_requests: LookupMap<VerifyForeignTransactionRequest, Vec<YieldIndex>>,
    proposed_updates: ProposedUpdates,
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

**File:** crates/contract/src/lib.rs (L2316-2322)
```rust
    pub fn return_verify_foreign_tx_and_clean_state_on_success(
        &mut self,
        request: VerifyForeignTransactionRequest,
        #[callback_result] response: Result<VerifyForeignTransactionResponse, PromiseError>,
    ) -> PromiseOrValue<VerifyForeignTransactionResponse> {
        match response {
            Ok(response) => PromiseOrValue::Value(response),
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

**File:** crates/near-mpc-sdk/src/foreign_chain.rs (L48-64)
```rust
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
```
