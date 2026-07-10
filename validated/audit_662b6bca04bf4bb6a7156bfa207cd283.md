### Title
`respond_verify_foreign_tx` Accepts Caller-Supplied `payload_hash` Without Binding It to the Pending `request`, Enabling a Byzantine Leader to Deliver a Forged Foreign-Chain Attestation - (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_verify_foreign_tx` verifies that `response.signature` is a valid ECDSA signature over `response.payload_hash`, but it never checks that `response.payload_hash` is the canonical hash of the `request` that is being resolved. A Byzantine attested MPC participant who acts as signing leader for any request B can reuse the valid `(payload_hash_B, sig_B)` pair to resolve a completely different pending request A, delivering a cryptographically valid but semantically wrong attestation to the user who submitted request A.

---

### Finding Description

In `respond` (regular signatures), the payload hash is read directly from the `request` struct — the same object used as the map key — so the verified hash is always bound to the pending request:

```rust
// crates/contract/src/lib.rs ~line 600
let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    payload_hash,          // ← derived from `request`, not from `response`
    &expected_public_key,
).is_ok()
```

In `respond_verify_foreign_tx`, the hash is taken from the **caller-supplied `response`** struct instead:

```rust
// crates/contract/src/lib.rs ~line 726-733
let payload_hash: [u8; 32] = response.payload_hash.0;   // ← from response, not from request

near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,
).is_ok()
```

After the signature check passes, the contract resolves the pending yields keyed on `request`:

```rust
// crates/contract/src/lib.rs ~line 749-753
pending_requests::resolve_yields_for(
    &mut self.pending_verify_foreign_tx_requests,
    &request,
    serde_json::to_vec(&response).unwrap(),   // ← response contains the wrong payload_hash
)
```

The contract never asserts `response.payload_hash == canonical_hash(request, extracted_values)`. The canonical hash is defined as `SHA-256(borsh(ForeignTxSignPayload))` (see `docs/foreign-chain-transactions.md`), but this recomputation is absent from the on-chain verification path.

---

### Impact Explanation

A Byzantine attested MPC leader for request B obtains a legitimately produced `(payload_hash_B, sig_B)` pair (by participating honestly in the threshold protocol). It then calls:

```
respond_verify_foreign_tx(request_A, { payload_hash: payload_hash_B, signature: sig_B })
```

- The signature check passes: `sig_B` is a valid ECDSA signature over `payload_hash_B` under the root key.
- The pending yield for `request_A` is drained and the user receives `{ payload_hash_B, sig_B }`.
- The user holds a cryptographically valid MPC attestation that claims to verify a **different** foreign-chain transaction (B) while believing it attests to their transaction (A).
- If a bridge contract accepts this attestation to authorize an inbound transfer, it processes the wrong transfer — a direct invalid bridge execution / double-spend condition.
- Request A is permanently consumed; the user cannot retry (the yield slot is gone), constituting a DoS on their request.

This matches the allowed High impact: **"forged foreign-chain verification… that causes invalid bridge execution or double-spend conditions."**

---

### Likelihood Explanation

The attack requires exactly one Byzantine attested MPC participant who happens to be elected leader for any concurrently pending request B. Leader election is deterministic and rotates across participants, so every participant will eventually be leader. No threshold collusion is needed: the Byzantine leader participates honestly in the threshold protocol (contributing its share like any other node), receives the assembled final signature, and then submits it against the wrong pending request. The only prerequisite is that at least two foreign-tx requests are pending simultaneously — a normal production condition.

---

### Recommendation

Recompute the expected `payload_hash` on-chain from the `request` fields and the `payload_version`, and assert equality with `response.payload_hash` before accepting the response. Concretely, the contract should compute:

```rust
let expected_hash = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
    request: request.request.clone(),
    values: response.extracted_values.clone(),   // nodes must also submit extracted values
}).compute_msg_hash()?;
assert_eq!(response.payload_hash, expected_hash, "payload_hash mismatch");
```

Alternatively, mirror the pattern used in `respond`: derive the hash from the `request` struct rather than accepting it from the `response`. The `VerifyForeignTransactionResponse` should not carry a free-form `payload_hash`; instead, the contract should reconstruct it deterministically.

---

### Proof of Concept

1. Alice submits `verify_foreign_transaction(request_A)` (e.g., Bitcoin tx `0xAAAA`). A pending yield is queued under key `request_A`.
2. Bob submits `verify_foreign_transaction(request_B)` (e.g., Bitcoin tx `0xBBBB`). A pending yield is queued under key `request_B`.
3. The MPC network runs the threshold protocol for request B. The Byzantine leader node assembles the final signature `sig_B` over `payload_hash_B = SHA-256(borsh(ForeignTxSignPayload{request_B, values_B}))`.
4. Instead of calling `respond_verify_foreign_tx(request_B, {payload_hash_B, sig_B})`, the Byzantine leader calls `respond_verify_foreign_tx(request_A, {payload_hash_B, sig_B})`.
5. On-chain: `verify_ecdsa_signature(sig_B, payload_hash_B, root_pk)` → **passes** (valid signature).
6. `resolve_yields_for(pending_verify_foreign_tx_requests, request_A, serialize({payload_hash_B, sig_B}))` → Alice's yield is resumed with the wrong attestation.
7. Alice's NEAR transaction returns `{payload_hash: payload_hash_B, signature: sig_B}` — a valid root-key signature attesting to Bitcoin tx `0xBBBB`, not `0xAAAA`.
8. If Alice's bridge contract trusts this attestation to release funds for `0xAAAA`, it processes the wrong inbound transfer.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** crates/contract/src/lib.rs (L718-753)
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

        pending_requests::resolve_yields_for(
            &mut self.pending_verify_foreign_tx_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
```

**File:** crates/contract/src/pending_requests.rs (L62-88)
```rust
/// Resume every yield queued for `request` with `response_bytes`, draining the
/// fan-out map in one pass. Returns `Err(RequestNotFound)` if the map held no entry.
///
/// Resuming a yield that has already timed out is a no-op at the SDK level.
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
