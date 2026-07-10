### Title
`respond_verify_foreign_tx` Verifies Signature Against Caller-Supplied `payload_hash` Without Binding It to the Pending Request - (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_verify_foreign_tx` checks that the submitted ECDSA signature is valid for the caller-supplied `response.payload_hash` under the root public key, but never independently derives or validates that `payload_hash` is the correct hash for the specific `request` being responded to. A single attested participant who leads a legitimate MPC signing session for request A can reuse the resulting `(hash_A, sig_A)` pair to resolve a completely different pending request B, corrupting its lifecycle.

---

### Finding Description

In `respond_verify_foreign_tx` (`crates/contract/src/lib.rs`, lines 718–734), the contract performs the following check:

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

`response.payload_hash` is entirely caller-supplied. The contract only verifies that the signature is cryptographically valid for that hash under the root key. It never computes or checks that `payload_hash` equals `SHA-256(borsh(ForeignTxSignPayload { request, values }))` for the specific `request` argument passed to the function.

This is the direct analog of the external report's vulnerability class: in the reference bug, `getSwapOutput` returns a value computed *after* fee deduction, but `_swap` checks `minOutput` *before* fee deduction — the check uses a value computed on a different basis than what is actually being compared. Here, the contract checks the signature against a `payload_hash` that is not derived from the `request` being resolved, creating the same kind of basis mismatch.

The node-side code in `build_signature_request` (`crates/node/src/providers/verify_foreign_tx/sign.rs`, lines 30–47) correctly constructs the payload hash from the request and extracted values, and uses a zero tweak (`Tweak::new([0u8; 32])`), meaning all foreign-tx signatures are produced under the **root** key. This is what makes cross-request reuse possible: any valid foreign-tx signature is valid under the same root key regardless of which request it was produced for.

The `ForeignTxSignPayload` does embed the `ForeignChainRpcRequest` in the signed data, so `hash_A ≠ hash_B` for different requests. However, the contract never enforces this binding — it accepts any `(payload_hash, signature)` pair where the signature is valid for the hash under the root key, regardless of whether the hash corresponds to the request being resolved.

---

### Impact Explanation

**Medium — request-lifecycle manipulation that breaks production safety/accounting invariants.**

Attack scenario:

1. Two pending requests exist: request A (`bitcoin_tx_id=0x1111`) and request B (`bitcoin_tx_id=0x2222`), both submitted by different bridge users.
2. The attacker is an attested participant and acts as the CaitSith coordinator (leader) for the signing round for request A. As coordinator, they assemble the final signature `sig_A` over `hash_A`.
3. Instead of submitting `respond_verify_foreign_tx(request=A, response={hash_A, sig_A})`, the attacker submits `respond_verify_foreign_tx(request=B, response={hash_A, sig_A})`.
4. The contract verifies: is `sig_A` valid for `hash_A` under the root key? **Yes** — the check passes.
5. Request B is resolved and removed from `pending_verify_foreign_tx_requests` with the response `{hash_A, sig_A}`.
6. The user of request B receives `{hash_A, sig_A}`. Their bridge contract calls `ForeignChainSignatureVerifier::verify_signature`, which computes `expected_hash_B` and finds `hash_A ≠ expected_hash_B` — verification fails.
7. Request B is consumed. The user cannot resubmit the same request (it is no longer pending). Their foreign-chain deposit is stuck.

For bridge operations (the primary use case of `verify_foreign_transaction`), this means a single attested participant can silently invalidate any pending bridge attestation request, causing the corresponding user deposit to be unrecoverable without manual intervention.

---

### Likelihood Explanation

**Medium-High.** Any attested participant who is elected as the CaitSith coordinator for a foreign-tx signing round obtains the complete assembled signature. Coordinator election happens regularly as part of normal protocol operation. The attacker does not need to collude with any other participant — they only need to be a single attested node below the signing threshold. The only precondition is that at least two foreign-tx requests are pending simultaneously, which is realistic for any active bridge deployment.

---

### Recommendation

The contract should bind the `payload_hash` to the `request` being resolved. Since the contract cannot independently recompute the hash (it does not have the extracted values), the recommended fix is to include the `request`'s canonical Borsh serialization as a domain-separation prefix in the signed payload, and then verify on-chain that the `payload_hash` starts with or is derived from a commitment to the specific `request`. Concretely:

- Change `ForeignTxSignPayload` to include the full `VerifyForeignTransactionRequest` (not just the `ForeignChainRpcRequest`) so that the signed hash is uniquely bound to the on-chain request key.
- In `respond_verify_foreign_tx`, recompute the request's canonical identifier and verify that `response.payload_hash` is consistent with it (e.g., by checking a prefix or by storing a commitment to the expected hash at request submission time).

Alternatively, store the `payload_hash` commitment on-chain when the request is first submitted (if the hash can be determined at that point), and compare it against `response.payload_hash` in `respond_verify_foreign_tx`.

---

### Proof of Concept

```
// State: two pending foreign-tx requests
pending_verify_foreign_tx_requests = {
    request_A (bitcoin_tx_id=0x1111): [yield_A],
    request_B (bitcoin_tx_id=0x2222): [yield_B],
}

// Attacker is coordinator for request_A's signing round.
// MPC network produces: hash_A = SHA256(borsh(ForeignTxSignPayload{request_A, values_A}))
//                        sig_A = ECDSA_sign(hash_A, root_key)

// Attacker submits respond_verify_foreign_tx with request=B but response for A:
respond_verify_foreign_tx(
    request = request_B,                          // different request
    response = { payload_hash: hash_A,            // hash of request A's payload
                 signature: sig_A }               // signature over hash_A
)

// Contract check (lib.rs:726-734):
//   payload_hash = response.payload_hash = hash_A
//   verify_ecdsa_signature(sig_A, hash_A, root_key) → OK  ← passes!
//   resolve_yields_for(pending_verify_foreign_tx_requests, request_B, response)
//   → yield_B resolved with {hash_A, sig_A}

// User B receives {hash_A, sig_A}
// ForeignChainSignatureVerifier::verify_signature:
//   expected_hash_B = SHA256(borsh(ForeignTxSignPayload{request_B, values_B}))
//   hash_A ≠ expected_hash_B → IncorrectPayloadSigned error
// User B's bridge deposit is stuck; request_B is consumed.
```

**Relevant code locations:**

- `respond_verify_foreign_tx` signature check (no request binding): [1](#0-0) 
- Node-side zero-tweak construction (all foreign-tx sigs use root key): [2](#0-1) 
- `ForeignTxSignPayload.compute_msg_hash` (hash the contract never recomputes): [3](#0-2) 
- SDK-side verification that does check payload binding (but only off-chain): [4](#0-3)

### Citations

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

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L1504-1509)
```rust
impl ForeignTxSignPayload {
    pub fn compute_msg_hash(&self) -> std::io::Result<Hash256> {
        let mut hasher = sha2::Sha256::new();
        borsh::BorshSerialize::serialize(self, &mut hasher)?;
        Ok(Hash256(hasher.finalize().into()))
    }
```

**File:** crates/near-mpc-sdk/src/foreign_chain.rs (L53-63)
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
```
