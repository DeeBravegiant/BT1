### Title
`respond_verify_foreign_tx` accepts a `payload_hash` not bound to the pending `request`, enabling a single malicious participant to forge foreign-chain verification results - (File: crates/contract/src/lib.rs)

### Summary

`respond_verify_foreign_tx` verifies only that `response.signature` is a valid ECDSA signature over `response.payload_hash` using the root public key. It does **not** verify that `response.payload_hash` is the hash of a payload derived from the supplied `request`. Any attested participant can call this function with a valid signature over an arbitrary payload hash, resolving a pending request with a fraudulent verification result.

### Finding Description

In `respond_verify_foreign_tx`, the signature check is:

```rust
let payload_hash: [u8; 32] = response.payload_hash.0;
// Check the signature is correct against the root public key
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,
).is_ok()
```

The `payload_hash` comes entirely from the caller-supplied `response`, not from the `request`. The contract never checks that `payload_hash == SHA-256(borsh(ForeignTxSignPayload::V1 { request: request.request, values: <actual_extracted_values> }))`.

Contrast this with the regular `respond` function, where the payload is taken directly from the `request` struct and the signature is verified against a key derived from the request's tweak — binding the signature cryptographically to the request:

```rust
let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    payload_hash,
    &expected_public_key,
).is_ok()
```

`respond_verify_foreign_tx` has no equivalent binding. Additionally, the contract does not restrict which attested participant may call `respond_verify_foreign_tx` — it only checks `assert_caller_is_attested_participant_and_protocol_active()`, not that the caller is the leader for the specific request.

**Attack path (single malicious node, below threshold):**

1. Malicious node M is elected leader for request B (`verify_foreign_transaction(bitcoin_tx_Y)`).
2. M participates in the threshold signing protocol for request B. Because all participants compute the final signature locally in the cait-sith protocol, M obtains the valid threshold signature `sig_B` over `payload_hash_B = SHA-256(borsh({request: bitcoin_tx_Y, values: [...]}))`.
3. A separate request A (`verify_foreign_transaction(bitcoin_tx_X)`) is pending in `pending_verify_foreign_tx_requests`.
4. M calls `respond_verify_foreign_tx(request=A, response={payload_hash: payload_hash_B, signature: sig_B})`.
5. The contract checks: (a) `sig_B` is valid over `payload_hash_B` ✓; (b) request A exists in the pending map ✓. It does **not** check that `payload_hash_B` corresponds to `request.request` (bitcoin_tx_X) ✗.
6. `resolve_yields_for` drains all yields queued under request A, delivering `{payload_hash_B, sig_B}` to every caller waiting on bitcoin_tx_X.

The callers for request A receive a `VerifyForeignTransactionResponse` whose `payload_hash` encodes bitcoin_tx_Y's extracted values, not bitcoin_tx_X's. The MPC signature over this hash is valid, so any downstream contract that verifies only the signature (without independently reconstructing the expected hash from the original request) will accept the fraudulent attestation.

### Impact Explanation

A single malicious attested participant (strictly below the signing threshold) can deliver a forged foreign-chain verification result to any pending `verify_foreign_transaction` request. A bridge contract that trusts the returned `payload_hash` without independently reconstructing it from the request it submitted will authorize transfers based on a fraudulent attestation — enabling double-spend or invalid bridge execution. This matches the **High** allowed impact: *"forged foreign-chain verification … that causes invalid bridge execution or double-spend conditions."*

### Likelihood Explanation

The attacker needs only to be elected leader for any one legitimate request (a normal operational event), obtain the resulting threshold signature, and then call `respond_verify_foreign_tx` targeting a different pending request. No collusion above one node is required. The contract imposes no per-request leader restriction on who may call `respond_verify_foreign_tx`.

### Recommendation

Recompute the expected `payload_hash` on-chain from `request` and the `response.payload_hash` by requiring the caller to also supply the `extracted_values`, then verify:

```rust
let expected_payload = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
    request: request.request.clone(),
    values: supplied_values,
});
let expected_hash = expected_payload.compute_msg_hash()?;
if expected_hash != response.payload_hash {
    return Err(RespondError::PayloadHashMismatch.into());
}
```

Alternatively, bind the `payload_hash` to the request key at enqueue time (storing the expected hash in the pending map) so `respond_verify_foreign_tx` can verify it without trusting the caller.

### Proof of Concept

The root cause is visible in the contract source. The `respond` function for regular signatures takes `payload_hash` from `request.payload` (line 600), binding it to the request. `respond_verify_foreign_tx` takes `payload_hash` from `response.payload_hash` (line 726), with no cross-check against `request.request`. [1](#0-0) [2](#0-1) [3](#0-2) 

The `VerifyForeignTransactionRequest` struct used as the map key contains only `{request, domain_id, payload_version}` — no caller identity, no nonce, no expected hash — confirming that any valid signature over any `payload_hash` resolves the pending yields. [4](#0-3)

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
