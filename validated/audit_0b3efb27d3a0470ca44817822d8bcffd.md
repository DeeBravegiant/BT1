### Title
`respond_verify_foreign_tx` Accepts Arbitrary `payload_hash` Without Binding It to the Stored Request — (`File: crates/contract/src/lib.rs`)

### Summary

`respond_verify_foreign_tx` verifies that the submitted signature is cryptographically valid for the caller-supplied `response.payload_hash`, but it never checks that `response.payload_hash` is the correct hash for the stored `VerifyForeignTransactionRequest`. A single Byzantine attested participant who holds a valid `(payload_hash, signature)` pair from any prior signing session can replay it to resolve an unrelated pending `verify_foreign_transaction` request, permanently consuming the yield with incorrect data.

### Finding Description

In `respond_verify_foreign_tx` (lines 718–734 of `crates/contract/src/lib.rs`), the contract performs:

```rust
let payload_hash: [u8; 32] = response.payload_hash.0;   // ← caller-supplied

// Check the signature is correct against the root public key
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,
)
.is_ok()
``` [1](#0-0) 

The check only confirms that `signature` is a valid ECDSA signature over `response.payload_hash` under the root public key. It does **not** verify that `response.payload_hash` equals `SHA-256(borsh(ForeignTxSignPayload{request, values}))` for the specific `request` stored in `pending_verify_foreign_tx_requests`.

Compare this with the regular `respond` path, where the payload is taken directly from the stored `SignatureRequest` (not from the response):

```rust
let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    payload_hash,          // ← from stored request, not from response
    &expected_public_key,
)
``` [2](#0-1) 

For foreign-tx signing, the node always uses a zero tweak, so the signing key is always the root key:

```rust
Ok(SignatureRequest {
    ...
    tweak: Tweak::new([0u8; 32]),   // ← zero tweak → root key
    ...
})
``` [3](#0-2) 

This means every `respond_verify_foreign_tx` signature is produced under the same root key. A valid `(payload_hash_A, signature_A)` pair produced during a legitimate signing session for `request_A` is cryptographically indistinguishable from a valid pair for `request_B` as far as the contract's verification logic is concerned.

Once the contract accepts the call, `resolve_yields_for` drains all pending yields for the submitted `request` key and delivers the incorrect response to every waiting caller:

```rust
pending_requests::resolve_yields_for(
    &mut self.pending_verify_foreign_tx_requests,
    &request,
    serde_json::to_vec(&response).unwrap(),
)
``` [4](#0-3) 

### Impact Explanation

A single Byzantine attested participant can permanently corrupt the lifecycle of any pending `verify_foreign_transaction` request:

1. The attacker participates in a legitimate signing session for `request_A` and retains the output `(payload_hash_A, signature_A)`.
2. A victim submits `verify_foreign_transaction(request_B)`, creating a pending yield.
3. The attacker calls `respond_verify_foreign_tx(request_B, {payload_hash_A, signature_A})`.
4. The contract's signature check passes (valid signature for `payload_hash_A` under the root key).
5. The pending yield for `request_B` is consumed and the victim receives `{payload_hash_A, signature_A}`.
6. The victim's bridge contract detects the mismatch (via `ForeignChainSignatureVerifier::verify_signature` in the SDK, which checks `expected_payload_hash == response.payload_hash`), but the yield is already consumed — the request cannot be retried. [5](#0-4) 

If the victim's bridge contract does not perform the `payload_hash` equality check and only verifies the ECDSA signature, it would accept the forged response as proof that `request_B`'s foreign transaction was verified, enabling invalid bridge execution.

This matches the **Medium** allowed impact: *request-lifecycle manipulation that breaks production safety/accounting invariants*.

### Likelihood Explanation

- Requires exactly one Byzantine attested participant — strictly below the signing threshold.
- The attacker only needs to have been the leader in one prior legitimate signing session to obtain a reusable `(payload_hash, signature)` pair.
- No special tooling is needed beyond calling a public contract method.
- The fan-out design (multiple callers sharing one pending-request slot) amplifies the impact: one malicious call can block all callers waiting on the same `request_B`. [6](#0-5) 

### Recommendation

Bind the `payload_hash` to the stored request inside the contract. The simplest approach is to include a unique, contract-assigned request nonce in `ForeignTxSignPayloadV1` so that a signature produced for one request cannot be replayed for another. Alternatively, the contract should reject any `respond_verify_foreign_tx` call where `response.payload_hash` does not begin with a deterministic prefix derived from the stored `request` fields (e.g., `SHA-256(borsh(request))`), even though the full `values` are unknown on-chain.

### Proof of Concept

1. Deploy the contract in running state with a `ForeignTx` domain.
2. Node A (honest leader) processes `request_A` (Bitcoin tx `[0x01; 32]`), produces `(payload_hash_A, signature_A)`, and calls `respond_verify_foreign_tx(request_A, ...)` successfully.
3. Node A (now acting maliciously) retains `(payload_hash_A, signature_A)`.
4. Victim submits `verify_foreign_transaction(request_B)` (Bitcoin tx `[0x02; 32]`).
5. Node A calls `respond_verify_foreign_tx(request_B, {payload_hash_A, signature_A})`.
6. The contract's `verify_ecdsa_signature(signature_A, payload_hash_A, root_pk)` returns `true`.
7. The pending yield for `request_B` is resolved with `{payload_hash_A, signature_A}`.
8. The victim receives a response whose `payload_hash` does not match `SHA-256(borsh({request_B, values_B}))`, permanently blocking their bridge flow. [7](#0-6)

### Citations

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

**File:** crates/contract/src/lib.rs (L3255-3263)
```rust
        // Then: both yields are queued under the single (caller-agnostic) request key.
        assert_eq!(
            contract
                .pending_verify_foreign_tx_requests
                .get(&request)
                .map(|q| q.len()),
            Some(2),
            "duplicate foreign-tx requests from different callers should fan out",
        );
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
