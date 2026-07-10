### Title
Missing Payload-Hash Binding Check in `respond_verify_foreign_tx` Enables Cross-Request Signature Replay - (File: crates/contract/src/lib.rs)

### Summary
`respond_verify_foreign_tx` verifies that a signature is valid over the caller-supplied `response.payload_hash`, but never checks that `payload_hash` was actually derived from the `request` being answered. A single Byzantine attested participant can replay a legitimately-produced MPC signature from one foreign-chain verification request to satisfy a completely different pending request, delivering a forged verification outcome to all waiting callers.

### Finding Description

`respond_verify_foreign_tx` performs two checks before resolving pending yields:

1. The ECDSA signature in `response` is valid over `response.payload_hash` using the MPC root public key.
2. The `request` key exists in `pending_verify_foreign_tx_requests`. [1](#0-0) 

What it does **not** check is that `response.payload_hash` is the canonical hash of `ForeignTxSignPayload { request: request.request, values: <observed values> }`. The `payload_hash` is supposed to commit to both the original `ForeignChainRpcRequest` and the extracted values the MPC nodes observed on the foreign chain: [2](#0-1) 

Because the contract stores only the `ForeignChainRpcRequest` in the pending map (not the expected `payload_hash`), it cannot reconstruct what hash the nodes should have signed. The contract therefore accepts any `payload_hash` that carries a valid MPC signature, regardless of which request it was originally produced for.

The client-side SDK does perform this binding check: [3](#0-2) 

But this check is in the SDK, not enforced by the contract. The contract is the trust anchor; bridge contracts that rely on the contract's acceptance as proof of correctness are exposed.

### Impact Explanation

A single Byzantine attested participant (strictly below the signing threshold) can:

1. Observe a legitimately produced `respond_verify_foreign_tx` call on-chain for request A (e.g., verifying Bitcoin tx X), extracting `(payload_hash_A, sig_A)`.
2. Submit a new `verify_foreign_transaction` for request B (e.g., Bitcoin tx Y that has not actually finalized, or a different block hash).
3. Immediately call `respond_verify_foreign_tx(request=B, response={payload_hash_A, sig_A})`.

The contract accepts because `sig_A` is a valid MPC signature over `payload_hash_A`, and request B exists in the pending map. All callers waiting on request B receive `{payload_hash_A, sig_A}` — a response that attests to tx X's block hash, not tx Y's. A bridge contract that does not independently re-derive and compare the expected `payload_hash` will treat this as a valid attestation of tx Y, enabling invalid bridge execution or double-spend conditions.

This matches the allowed impact: **High — forged foreign-chain verification that causes invalid bridge execution or double-spend conditions.**

### Likelihood Explanation

- Requires the attacker to be a single attested MPC participant (not threshold collusion).
- A valid `(payload_hash, sig)` pair for any prior request is sufficient; these are published on-chain in every `respond_verify_foreign_tx` call.
- The attacker controls the timing: they can front-run the legitimate response for request B by submitting the replayed response first.
- Bridge contracts that omit the client-side `payload_hash` binding check (not using `ForeignChainSignatureVerifier::verify_signature`) are directly exploitable.

### Recommendation

The contract must bind the response to the request before accepting it. Since the contract does not store extracted values, the response should include the full `ForeignTxSignPayload` (request + extracted values). The contract then:

1. Recomputes `expected_hash = SHA-256(borsh(ForeignTxSignPayload { request: request.request, values: response.values }))`.
2. Asserts `expected_hash == response.payload_hash`.
3. Verifies the signature over `expected_hash`.

This ensures a signature produced for request A cannot satisfy request B, because the `request` field inside the payload would not match.

### Proof of Concept

**Setup:** MPC network is running. Attacker is one attested participant. Two users submit requests.

```
// Step 1: User-1 submits verify_foreign_transaction for Bitcoin tx X
verify_foreign_transaction({ request: Bitcoin(tx_id=X, ...), domain_id: D, ... })
// → pending_verify_foreign_tx_requests[key_X] = [yield_1]

// Step 2: MPC network legitimately processes request X and posts on-chain:
respond_verify_foreign_tx(
    request = { request: Bitcoin(tx_id=X, ...), domain_id: D },
    response = { payload_hash: H_X, signature: sig_X }
)
// H_X = SHA-256(borsh(ForeignTxSignPayload { request: Bitcoin(tx_id=X), values: [BlockHash(0xabc...)] }))
// sig_X = valid MPC ECDSA signature over H_X

// Step 3: Attacker (single attested participant) submits a new request for tx Y
verify_foreign_transaction({ request: Bitcoin(tx_id=Y, ...), domain_id: D, ... })
// → pending_verify_foreign_tx_requests[key_Y] = [yield_2]

// Step 4: Attacker calls respond_verify_foreign_tx with the REPLAYED response from Step 2
respond_verify_foreign_tx(
    request = { request: Bitcoin(tx_id=Y, ...), domain_id: D },  // ← request B
    response = { payload_hash: H_X, signature: sig_X }           // ← response from request A
)

// Contract checks:
// ✓ sig_X is valid over H_X using MPC root key  (it was produced legitimately for tx X)
// ✓ key_Y exists in pending_verify_foreign_tx_requests
// → resolve_yields_for(key_Y, serialize({payload_hash: H_X, sig_X}))
// → yield_2 resolves with H_X (which encodes tx X's block hash, not tx Y's)
```

The caller of `verify_foreign_transaction` for tx Y receives `VerifyForeignTransactionResponse { payload_hash: H_X, signature: sig_X }`. Any bridge contract that does not independently verify `H_X == SHA-256(borsh(ForeignTxSignPayload { request: Bitcoin(tx_id=Y), values: expected }))` will accept this as proof that tx Y was verified. [4](#0-3) [5](#0-4)

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

**File:** crates/near-mpc-sdk/src/foreign_chain.rs (L53-64)
```rust
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
