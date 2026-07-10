### Title
`respond_verify_foreign_tx` Does Not Bind `response.payload_hash` to `request`, Enabling Cross-Request Replay by a Single Byzantine MPC Node - (File: crates/contract/src/lib.rs)

---

### Summary

`respond_verify_foreign_tx` verifies that the submitted ECDSA signature is valid for the caller-supplied `response.payload_hash`, but never checks that `payload_hash` was actually derived from the `request` argument. A single attested MPC node (below threshold) that previously led any foreign-tx signing session retains a complete `(payload_hash, signature)` pair and can replay it against any currently-pending `verify_foreign_transaction` request, draining all queued yields with forged data and permanently preventing the correct response from being delivered.

---

### Finding Description

In `respond_verify_foreign_tx` the contract performs three checks:

1. Caller is an attested participant (`assert_caller_is_signer` + `assert_caller_is_attested_participant_and_protocol_active`) ✓
2. `response.signature` is a valid ECDSA signature over `response.payload_hash` under the **root** public key ✓
3. **`response.payload_hash` is bound to `request`** ✗ — this check is entirely absent [1](#0-0) 

The `payload_hash` is taken verbatim from the caller-supplied response:

```rust
let payload_hash: [u8; 32] = response.payload_hash.0;
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,   // ← caller-controlled, never checked against `request`
    &secp_pk,
).is_ok()
```

Compare this to `respond`, which correctly derives the hash from the request itself before verifying: [2](#0-1) 

```rust
let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    payload_hash,   // ← derived from request, not from response
    &expected_public_key,
).is_ok()
```

The correct `payload_hash` for a foreign-tx response is `SHA-256(borsh(ForeignTxSignPayload{request, extracted_values}))`: [3](#0-2) 

Because `request` is embedded inside the payload, `payload_hash_A` (produced for request A / tx_id X) is cryptographically distinct from `payload_hash_B` (for request B / tx_id Y). The contract never enforces this distinction.

Once `resolve_yields_for` is called with the forged response it **removes the pending-request entry and resumes every queued yield** in one pass: [4](#0-3) 

After that, any subsequent call with the correct response returns `Err(RequestNotFound)` — the correct answer can never reach the waiting callers.

**Attack path (single Byzantine node M, below threshold):**

1. M legitimately leads a signing session for request A (Bitcoin tx_id X), obtaining `(payload_hash_A, sig_A)`.
2. A user submits `verify_foreign_transaction` for request B (Bitcoin tx_id Y); one or more yields are queued.
3. M calls `respond_verify_foreign_tx(request = B, response = {payload_hash_A, sig_A})`.
4. The contract accepts: `sig_A` is a valid root-key signature over `payload_hash_A` ✓.
5. All yields for request B are drained with `{payload_hash_A, sig_A}`.
6. Users receive a `VerifyForeignTransactionResponse` whose `payload_hash` encodes tx_id X's data, not tx_id Y's.
7. The correct response for request B can never be submitted.

---

### Impact Explanation

Every caller of `verify_foreign_transaction` for request B receives a `VerifyForeignTransactionResponse` whose `payload_hash` encodes a **different** foreign-chain transaction's data. Any downstream bridge contract that uses this response to authorize a cross-chain action (e.g., releasing funds upon proof that a Bitcoin deposit was confirmed in a specific block) would be operating on forged attestation data. This directly enables invalid bridge execution and potential double-spend conditions.

The SDK's `ForeignChainSignatureVerifier::verify_signature` does check `payload_hash` against expected extracted values: [5](#0-4) 

However, this is a client-side helper that requires the caller to already know the expected extracted values — precisely the information the `verify_foreign_transaction` flow is meant to provide. Callers that rely on the contract's response to learn the extracted values cannot independently detect the forgery.

---

### Likelihood Explanation

Requires exactly one Byzantine MPC node that has previously been the leader for any foreign-tx signing session. The leader node assembles the final threshold signature and calls `respond_verify_foreign_tx`, so it retains the complete `(payload_hash, signature)` pair. Any such node can replay that pair against any future pending request at any time. No threshold collusion, no key leakage, and no network-level attack is required — a single below-threshold participant suffices.

---

### Recommendation

The contract must verify that `response.payload_hash` is consistent with `request`. The cleanest fix mirrors what `respond` already does: include the `extracted_values` in the response and recompute `payload_hash` on-chain:

```rust
// In respond_verify_foreign_tx, after receiving extracted_values in the response:
let expected_hash = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
    request: request.request.clone(),
    values: response.extracted_values.clone(),
}).compute_msg_hash()?;

if expected_hash != response.payload_hash {
    return Err(RespondError::PayloadHashMismatch.into());
}
```

This binds the signature to the specific `request` and prevents cross-request replay.

---

### Proof of Concept

```rust
// Precondition: malicious node M previously led signing for request_A,
// retaining (payload_hash_A, sig_A) from that session.

// 1. Victim user queues a request for a different tx
contract.verify_foreign_transaction(request_args_B);

// 2. M submits the forged response — passes all contract checks:
//    - assert_caller_is_signer(): M is an attested participant ✓
//    - verify_ecdsa_signature(sig_A, payload_hash_A, root_pk): valid ✓
//    - payload_hash_A bound to request_B: NOT CHECKED ✗
contract.respond_verify_foreign_tx(
    request_B,
    VerifyForeignTransactionResponse {
        payload_hash: payload_hash_A,  // encodes tx_id X, not tx_id Y
        signature:    sig_A,
    },
).expect("accepted — sig_A is a valid root-key signature");

// 3. resolve_yields_for drains all yields for request_B with forged data.
//    Correct response for request_B now returns Err(RequestNotFound).
assert!(contract.get_pending_verify_foreign_tx_request(&request_B).is_none());
```

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

**File:** crates/contract/src/pending_requests.rs (L66-88)
```rust
pub(crate) fn resolve_yields_for<K>(
    requests: &mut LookupMap<K, Vec<YieldIndex>>,
    request: &K,
    response_bytes: Vec<u8>,
) -> Result<(), Error>
where
    K: BorshSerialize + BorshDeserialize + Clone + Ord,
{
    let resumed = requests
        .remove(request)
        .unwrap_or_default()
        .into_iter()
        .map(|YieldIndex { data_id }| {
            env::promise_yield_resume(&data_id, response_bytes.clone());
        })
        .count();

    if resumed > 0 {
        Ok(())
    } else {
        Err(InvalidParameters::RequestNotFound.into())
    }
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
