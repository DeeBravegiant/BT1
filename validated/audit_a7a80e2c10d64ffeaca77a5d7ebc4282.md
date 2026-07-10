### Title
Missing Payload-Hash-to-Request Binding in `respond_verify_foreign_tx` Enables Single-Node Forged Foreign-Chain Verification - (File: `crates/contract/src/lib.rs`)

---

### Summary

The `respond_verify_foreign_tx` contract function verifies that the submitted signature is cryptographically valid for `response.payload_hash`, but never verifies that `response.payload_hash` is the correct hash for the given `request`. A single malicious MPC node acting as signing coordinator can reuse a valid threshold signature obtained from any prior legitimate session to resolve a completely different pending foreign-tx verification request with an attacker-chosen payload hash, delivering a forged attestation to the bridge consumer.

---

### Finding Description

In `respond_verify_foreign_tx` the contract performs three checks:

1. Caller is an attested participant
2. `verify_ecdsa_signature(signature_response, &payload_hash, &secp_pk)` — the signature is valid for `response.payload_hash`
3. `resolve_yields_for(&mut self.pending_verify_foreign_tx_requests, &request, ...)` — the request is pending [1](#0-0) 

The critical missing check: the contract never asserts that

```
response.payload_hash == SHA-256(borsh(ForeignTxSignPayload::V1 {
    request: request.request,
    values: <observed_values>,
}))
```

`payload_hash` is taken verbatim from the caller-supplied `response` struct. The contract has no way to reconstruct the expected hash on-chain (it does not know the extracted values), so the binding is entirely absent. [2](#0-1) 

By contrast, the regular `respond` function derives the expected payload directly from the on-chain `request.payload`, making substitution impossible: [3](#0-2) 

The canonical payload structure that should bind the hash to the request is defined here: [4](#0-3) 

The SDK-side verifier (`ForeignChainSignatureVerifier::verify_signature`) does perform this check — but it is client-side only, not enforced on-chain: [5](#0-4) 

---

### Impact Explanation

A single malicious MPC node that is selected as signing coordinator for **any** request_A can:

1. Run the legitimate MPC signing protocol for request_A, assembling `signature_A` — a valid threshold signature over `payload_hash_A`
2. Call `respond_verify_foreign_tx(request_B, {payload_hash: payload_hash_A, signature: signature_A})` for any other pending request_B
3. The contract accepts: `verify_ecdsa_signature(signature_A, payload_hash_A, root_pk)` passes; request_B is in `pending_verify_foreign_tx_requests`; no binding check exists
4. The NEAR promise for request_B resolves with `payload_hash_A` — an attestation for the wrong foreign-chain state

A bridge contract consuming this response without re-running the SDK's `verify_signature` would accept a forged attestation, enabling invalid bridge execution (e.g., minting tokens for a transaction that never occurred, or for a different transaction than the one the user requested). This matches the **High** impact class: *forged foreign-chain verification that causes invalid bridge execution*.

---

### Likelihood Explanation

- Requires exactly **one** malicious MPC node to be selected as coordinator for any signing session — strictly below the signing threshold
- The coordinator role is assigned deterministically; an adversary controlling one node can wait for their turn
- No signature forgery is needed — the attacker reuses a legitimately assembled threshold signature from a prior session
- The contract has no nonce, timestamp, or request-binding check to prevent cross-request signature reuse
- The node must be an attested participant, but that is the normal operating condition for any active MPC node

---

### Recommendation

Bind the signature to the specific request on-chain. Concrete options:

1. **Include the request identifier in the signed payload** so that a signature over `payload_hash_A` is cryptographically unusable for request_B. For example, incorporate `request.request` (the `ForeignChainRpcRequest`) into the `ForeignTxSignPayload` in a way that the contract can verify without knowing the extracted values (e.g., sign `SHA-256(request_bytes || extracted_values_hash)` and have the contract verify the `request_bytes` prefix).
2. **Require the responder to submit the extracted values** alongside the response, and have the contract recompute and verify `payload_hash` on-chain.
3. At minimum, **enforce SDK-side verification contractually** — require consuming contracts to call `ForeignChainSignatureVerifier::verify_signature` and make the interface impossible to misuse without it.

---

### Proof of Concept

```
1. Alice submits verify_foreign_transaction(request_A)
   — e.g., Bitcoin tx_id_A, 6 confirmations, BlockHash extractor

2. MPC nodes query Bitcoin RPC; coordinator assembles:
     payload_hash_A = SHA-256(borsh(ForeignTxSignPayload::V1{request_A, [BlockHash(block_A)]}))
     signature_A    = threshold_sign(payload_hash_A)
   Coordinator submits respond_verify_foreign_tx(request_A, {payload_hash_A, signature_A})
   → Alice's promise resolves correctly.

3. Bob submits verify_foreign_transaction(request_B)
   — e.g., Bitcoin tx_id_B (a different or non-existent transaction)

4. Malicious coordinator calls:
     respond_verify_foreign_tx(request_B, {payload_hash: payload_hash_A, signature: signature_A})

5. Contract checks:
     verify_ecdsa_signature(signature_A, payload_hash_A, root_pk) → OK  ✓
     pending_verify_foreign_tx_requests.contains(request_B)       → OK  ✓
     payload_hash_A == expected_hash_for_request_B                → NOT CHECKED ✗

6. Bob's promise resolves with {payload_hash_A, signature_A}.

7. Bob's bridge contract receives the response. If it does not call
   ForeignChainSignatureVerifier::verify_signature, it treats payload_hash_A
   as a valid attestation for request_B and executes the bridge action
   for a transaction that was never verified.
```

Root cause line: [2](#0-1) 

Reachable by a single unprivileged (but attested) MPC node acting as coordinator, strictly below the signing threshold.

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

**File:** crates/contract/src/lib.rs (L718-747)
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

**File:** crates/near-mpc-sdk/src/foreign_chain.rs (L57-64)
```rust
        let payload_is_correct = expected_payload_hash == response.payload_hash;

        if !payload_is_correct {
            return Err(VerifyForeignChainError::IncorrectPayloadSigned {
                got: response.payload_hash.clone(),
                expected: expected_payload_hash,
            });
        }
```
