### Title
Unvalidated `payload_hash` in `respond_verify_foreign_tx` Enables Cross-Request Signature Replay — (File: `crates/contract/src/lib.rs`)

### Summary

`respond_verify_foreign_tx` verifies that `response.signature` is a valid ECDSA signature over `response.payload_hash`, but never validates that `response.payload_hash` is actually derived from the submitted `request`. A single malicious attested participant can reuse a legitimately produced threshold signature for one pending foreign-tx request to resolve a *different* pending request, delivering a forged `payload_hash` to the waiting caller.

### Finding Description

In `respond_verify_foreign_tx`, the contract performs two independent checks:

1. Verifies `response.signature` over `response.payload_hash` against the root public key.
2. Uses `request` as a lookup key to drain the pending yield queue. [1](#0-0) 

The `payload_hash` in the response is taken directly from the caller-supplied `response` argument and is never cross-checked against the `request` field. The contract has no mechanism to enforce that `payload_hash == SHA-256(borsh(ForeignTxSignPayload { request, values }))` for the specific `request` that was queued.

The `VerifyForeignTransactionResponse` struct carries only `payload_hash` and `signature`; the `extracted_values` that would allow on-chain recomputation are absent. [2](#0-1) 

The intended off-chain verification is delegated entirely to the SDK's `ForeignChainSignatureVerifier::verify_signature`, which checks `expected_payload_hash == response.payload_hash`. [3](#0-2) 

The contract itself enforces no such binding.

### Impact Explanation

A single malicious attested participant who co-signs a legitimate threshold signature for **request B** can call `respond_verify_foreign_tx(request = request_A, response = {payload_hash_B, sig_B})`. The contract accepts this because:

- `sig_B` is a valid signature over `payload_hash_B` under the root key ✓
- `request_A` exists in `pending_verify_foreign_tx_requests` ✓
- The caller is an attested participant ✓ [4](#0-3) 

The yield for `request_A` is resolved with `{payload_hash_B, sig_B}`. The user who submitted `request_A` receives a `VerifyForeignTransactionResponse` whose `payload_hash` encodes the foreign-chain data of a *different* transaction. Any bridge service that does not independently recompute and compare the expected `payload_hash` (i.e., does not use the SDK verifier) will accept this as a valid attestation of `request_A`'s transaction, enabling forged foreign-chain verification and potential double-spend or invalid bridge execution.

**Impact class**: High — cross-chain replay / forged foreign-chain verification bypass causing invalid bridge execution.

### Likelihood Explanation

The attacker is a single attested MPC participant (strictly below the signing threshold). No key forgery is required; the attacker only needs to:

1. Participate honestly in the threshold signing of any request B (obtaining a valid `(payload_hash_B, sig_B)`).
2. Call `respond_verify_foreign_tx` with `request = request_A` and the reused response.

Any participant is eligible to submit `respond_verify_foreign_tx`; the contract does not restrict which participant may call it. [5](#0-4) 

Two concurrent pending requests are sufficient, which is a normal production condition for any active bridge.

### Recommendation

The contract must bind `payload_hash` to `request`. The cleanest fix is to include `extracted_values` in the `VerifyForeignTransactionResponse` so the contract can recompute and assert:

```
payload_hash == SHA-256(borsh(ForeignTxSignPayload { request, extracted_values }))
```

before accepting the response. Alternatively, the `ForeignTxSignPayload` itself (not just its hash) can be included in the response, allowing the contract to verify both the hash binding and the signature in one step. The `respond` method for plain signatures avoids this problem because the payload is committed inside the `SignatureRequest` key itself. [6](#0-5) 

### Proof of Concept

1. User A calls `verify_foreign_transaction(request_A)` — queued under key `request_A`.
2. User B calls `verify_foreign_transaction(request_B)` — queued under key `request_B`.
3. The MPC network processes `request_B`; the leader and ≥ threshold followers co-sign `payload_hash_B`. The leader obtains `(payload_hash_B, sig_B)`.
4. The malicious leader calls `respond_verify_foreign_tx(request = request_A, response = {payload_hash_B, sig_B})`.
5. The contract verifies `sig_B` over `payload_hash_B` — valid ✓. It finds `request_A` in the pending map ✓. It resolves the yield for `request_A` with `{payload_hash_B, sig_B}`.
6. User A's awaiting NEAR transaction receives `{payload_hash_B, sig_B}`. A bridge service that omits the SDK's `verify_signature` check accepts this as proof that `request_A`'s foreign-chain transaction was verified, while the signed data actually attests to `request_B`'s transaction. [7](#0-6) [8](#0-7)

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

**File:** crates/contract/src/lib.rs (L697-705)
```rust
        let signer = Self::assert_caller_is_signer();

        log!(
            "respond_verify_foreign_tx: signer={}, request={:?}",
            &signer,
            &request
        );

        self.assert_caller_is_attested_participant_and_protocol_active();
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

**File:** crates/contract/src/lib.rs (L749-753)
```rust
        pending_requests::resolve_yields_for(
            &mut self.pending_verify_foreign_tx_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L124-128)
```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L147-150)
```rust
pub struct VerifyForeignTransactionResponse {
    pub payload_hash: Hash256,
    pub signature: SignatureResponse,
}
```

**File:** crates/near-mpc-sdk/src/foreign_chain.rs (L57-63)
```rust
        let payload_is_correct = expected_payload_hash == response.payload_hash;

        if !payload_is_correct {
            return Err(VerifyForeignChainError::IncorrectPayloadSigned {
                got: response.payload_hash.clone(),
                expected: expected_payload_hash,
            });
```
