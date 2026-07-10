### Title
Missing `payload_hash`-to-`request` Binding in `respond_verify_foreign_tx` Enables Cross-Request Signature Replay by a Single Byzantine Participant - (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_verify_foreign_tx` verifies that `response.signature` is cryptographically valid over `response.payload_hash`, but never checks that `response.payload_hash` was actually derived from the `request` being answered. A single Byzantine attested participant who has observed any prior legitimate MPC signing can replay that signature against any pending `verify_foreign_transaction` request, delivering a structurally valid but semantically wrong attestation to the caller.

---

### Finding Description

In `respond_verify_foreign_tx` the only cryptographic check performed is:

```rust
// crates/contract/src/lib.rs lines 726-734
let payload_hash: [u8; 32] = response.payload_hash.0;
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,
)
.is_ok()
``` [1](#0-0) 

The contract confirms that `signature` is a valid ECDSA signature over `payload_hash` under the domain's root public key. It then resolves all queued yields for `request` with the full `response` blob:

```rust
// crates/contract/src/lib.rs lines 749-753
pending_requests::resolve_yields_for(
    &mut self.pending_verify_foreign_tx_requests,
    &request,
    serde_json::to_vec(&response).unwrap(),
)
``` [2](#0-1) 

There is **no check** that `response.payload_hash` equals `SHA-256(borsh(ForeignTxSignPayload { request, values }))` for the `request` being answered. The `payload_hash` is a free parameter supplied by the caller of `respond_verify_foreign_tx`.

Contrast this with the regular `respond` function, where the payload is embedded inside the `SignatureRequest` struct itself and the signature is verified against `request.payload.as_ecdsa()` — the payload is structurally bound to the request key and cannot be substituted:

```rust
// crates/contract/src/lib.rs lines 600-608
let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    payload_hash,
    &expected_public_key,
)
.is_ok()
``` [3](#0-2) 

For `respond_verify_foreign_tx`, the `VerifyForeignTransactionRequest` contains only `domain_id`, `payload_version`, and `request` (the chain-specific query). The `payload_hash` lives exclusively in `VerifyForeignTransactionResponse` and is never validated against the request:

```rust
// crates/near-mpc-contract-interface/src/types/foreign_chain.rs
pub struct VerifyForeignTransactionResponse {
    pub payload_hash: Hash256,
    pub signature: SignatureResponse,
}
``` [4](#0-3) 

The correct `payload_hash` for a given request is `SHA-256(borsh(ForeignTxSignPayload::V1 { request, values }))`:

```rust
// crates/near-mpc-contract-interface/src/types/foreign_chain.rs lines 1504-1509
impl ForeignTxSignPayload {
    pub fn compute_msg_hash(&self) -> std::io::Result<Hash256> {
        let mut hasher = sha2::Sha256::new();
        borsh::BorshSerialize::serialize(self, &mut hasher)?;
        Ok(Hash256(hasher.finalize().into()))
    }
}
``` [5](#0-4) 

The contract never recomputes or checks this hash against the `request` parameter.

---

### Impact Explanation

A single Byzantine attested participant can execute a **cross-request signature replay**:

1. Participant legitimately takes part in the MPC threshold signing for request Y (e.g., Bitcoin tx Y). The network produces `SIG_Y` over `H_Y = SHA-256(borsh(ForeignTxSignPayload { request_Y, values_Y }))`. The participant observes `(H_Y, SIG_Y)`.

2. A separate pending request X exists on-chain (submitted by a bridge contract or user).

3. The attacker calls `respond_verify_foreign_tx(request=X, response={payload_hash=H_Y, signature=SIG_Y})`.

4. The contract passes the signature check (valid ECDSA over `H_Y`), finds request X in `pending_verify_foreign_tx_requests`, and resolves all queued yields with `{payload_hash=H_Y, signature=SIG_Y}`.

5. The caller of request X receives a response whose `payload_hash` encodes the extracted values of transaction Y, not transaction X.

**Scenario A — caller uses the SDK's `verify_signature`**: The SDK checks `expected_payload_hash == response.payload_hash` and rejects the mismatch:

```rust
// crates/near-mpc-sdk/src/foreign_chain.rs lines 57-63
let payload_is_correct = expected_payload_hash == response.payload_hash;
if !payload_is_correct {
    return Err(VerifyForeignChainError::IncorrectPayloadSigned { ... });
}
``` [6](#0-5) 

The caller's request is consumed and must be resubmitted — a targeted denial-of-service against any specific pending request.

**Scenario B — caller only checks signature validity** (does not use the SDK or omits the `payload_hash` binding check): The caller receives a structurally valid signature over a hash that encodes the wrong transaction's extracted values. A bridge contract that gates token minting on this attestation would accept a forged proof that transaction X finalized with values it never had, enabling unauthorized bridge execution or double-spend.

This maps directly to the allowed impact: *"High. Cross-chain replay, forged foreign-chain verification, light-client-style verification bypass, or participant/attestation authorization bypass that causes invalid bridge execution or double-spend conditions."*

---

### Likelihood Explanation

- **Attacker role**: A single attested MPC participant — below the signing threshold. No collusion required.
- **Prerequisite**: The attacker must have observed at least one prior legitimate MPC signing for any request on the same domain. This is trivially satisfied for any active participant.
- **Opportunity**: Any pending `verify_foreign_transaction` request is a target. The attacker can race to call `respond_verify_foreign_tx` before the honest leader does.
- **Caller exposure**: Bridge contracts that do not use `ForeignChainSignatureVerifier::verify_signature` from the SDK, or that trust `payload_hash` without recomputing the expected hash from known extracted values, are directly exploitable for forged attestations.

Likelihood is **Medium**: the attacker must be an active attested participant, but no threshold cooperation, key material, or privileged access is needed beyond normal protocol participation.

---

### Recommendation

The contract must verify that `response.payload_hash` is consistent with the `request` being answered. Because the `values` (extracted chain data) are not stored on-chain, the fix requires including the `ForeignTxSignPayload` (or at minimum the `Vec<ExtractedValue>`) in the `respond_verify_foreign_tx` call arguments, then recomputing and asserting:

```rust
let expected_hash = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
    request: request.request.clone(),
    values: response_values,
}).compute_msg_hash()?;
assert_eq!(expected_hash, response.payload_hash, "payload_hash does not match request");
```

This is exactly the check the SDK-side `ForeignChainSignatureVerifier::verify_signature` performs for callers — it must also be enforced at the contract level so that no single Byzantine participant can bypass it. [7](#0-6) 

---

### Proof of Concept

```
Setup:
  - MPC network with 3 participants, threshold 2.
  - Domain 0 is a ForeignTx (Secp256k1) domain.
  - Attacker controls participant P1 (one of the three).

Step 1 — Obtain a valid signature for request Y:
  User submits verify_foreign_transaction(Bitcoin tx_id=Y, extractors=[BlockHash]).
  All three nodes verify and sign. P1 observes the final response:
    H_Y = SHA-256(borsh(ForeignTxSignPayload::V1 { request_Y, values_Y=[block_hash_Y] }))
    SIG_Y = threshold_sign(H_Y)

Step 2 — Target request X:
  Bridge contract submits verify_foreign_transaction(Bitcoin tx_id=X, extractors=[BlockHash]).
  Request X is now pending in pending_verify_foreign_tx_requests.

Step 3 — Replay:
  P1 calls respond_verify_foreign_tx(
    request = VerifyForeignTransactionRequest { domain_id=0, request=X, ... },
    response = VerifyForeignTransactionResponse { payload_hash=H_Y, signature=SIG_Y }
  )

Step 4 — Contract accepts:
  verify_ecdsa_signature(SIG_Y, H_Y, root_pk) → Ok   ✓
  pending_verify_foreign_tx_requests[X] resolved with {payload_hash=H_Y, signature=SIG_Y}

Step 5 — Bridge contract receives:
  payload_hash = H_Y  (encodes block_hash of tx Y, not tx X)
  signature   = SIG_Y (valid ECDSA over H_Y)
  If bridge only calls verify_ecdsa_signature(SIG_Y, H_Y, root_pk) → passes.
  Bridge mints tokens as if tx X finalized with block_hash_Y — forged attestation accepted.
``` [8](#0-7)

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

**File:** crates/contract/src/lib.rs (L749-753)
```rust
        pending_requests::resolve_yields_for(
            &mut self.pending_verify_foreign_tx_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L1499-1502)
```rust
pub struct ForeignTxSignPayloadV1 {
    pub request: ForeignChainRpcRequest,
    pub values: Vec<ExtractedValue>,
}
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

**File:** crates/near-mpc-sdk/src/foreign_chain.rs (L47-64)
```rust
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
```
