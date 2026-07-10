### Title
Response-Request Binding Missing in `respond_verify_foreign_tx`: Attacker-Controlled `payload_hash` Permanently Consumes Wrong Pending Request — (`File: crates/contract/src/lib.rs`)

---

### Summary

`respond_verify_foreign_tx` verifies the submitted signature against `response.payload_hash`, a value supplied entirely by the calling node, rather than against a hash derived from the `VerifyForeignTransactionRequest` stored on-chain. A single Byzantine attested participant (strictly below the signing threshold) can therefore call `respond_verify_foreign_tx` with a valid signature for transaction TX_X while naming a pending request for a completely different transaction TX_Y. The contract accepts the call, permanently drains TX_Y's yield queue, and delivers a response whose `payload_hash` does not correspond to TX_Y. TX_Y's request is irrecoverably consumed.

---

### Finding Description

In `respond` (the regular sign path), the payload used for signature verification is taken from the **request** stored on-chain:

```rust
// crates/contract/src/lib.rs ~line 600
let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    payload_hash,          // ← bound to the request
    &expected_public_key,
)
```

In `respond_verify_foreign_tx`, the payload used for signature verification is taken from the **response** supplied by the calling node:

```rust
// crates/contract/src/lib.rs ~line 726-733
let payload_hash: [u8; 32] = response.payload_hash.0;   // ← attacker-controlled

near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,          // ← NOT derived from `request`
    &secp_pk,
)
.is_ok()
```

After the signature check passes, `resolve_yields_for` is called with the **request** key, permanently removing TX_Y's pending entry and resuming all its yields with the mismatched response:

```rust
// crates/contract/src/lib.rs ~line 749-753
pending_requests::resolve_yields_for(
    &mut self.pending_verify_foreign_tx_requests,
    &request,                                    // TX_Y's key
    serde_json::to_vec(&response).unwrap(),      // TX_X's payload_hash + sig
)
```

The contract never checks that `response.payload_hash` equals the hash that should be derived from `request`'s transaction data. The two values are completely decoupled.

---

### Impact Explanation

A single Byzantine attested participant (one node, below threshold) can:

1. Obtain a valid threshold signature for TX_X through the normal MPC flow.
2. Observe any pending `verify_foreign_transaction` request for TX_Y on-chain.
3. Call `respond_verify_foreign_tx(request_for_TX_Y, {payload_hash: hash(TX_X), signature: sig_TX_X})`.
4. The contract verifies `sig_TX_X` is valid for `hash(TX_X)` — passes.
5. `resolve_yields_for` permanently removes TX_Y's entry and resumes its yields with the wrong response.

**Consequences:**
- TX_Y's pending request is irrecoverably consumed; the user cannot retry.
- The user's yield callback receives a `VerifyForeignTransactionResponse` whose `payload_hash` is for TX_X, not TX_Y.
- If the downstream application does not re-validate `payload_hash` against the expected transaction, it may use the mismatched signature to execute TX_X on the foreign chain — a direct cross-chain execution-flow manipulation.
- Even if the application does validate and rejects the response, TX_Y's request slot is permanently destroyed, breaking the request lifecycle invariant.

This matches the allowed impact: **"request-lifecycle, or contract execution-flow manipulation that breaks production safety/accounting invariants."**

---

### Likelihood Explanation

- Requires only one Byzantine attested participant — strictly below the signing threshold.
- No collusion, no TEE break, no network-level attack needed.
- All pending `verify_foreign_transaction` requests are publicly visible on-chain.
- Any previously obtained valid threshold signature (for any TX_X) can be reused as the weapon.
- The attack is deterministic and repeatable.

---

### Recommendation

Derive `payload_hash` from the **request** stored on-chain, not from the response. The contract should compute the expected payload hash from `request`'s transaction data and assert equality before verifying the signature:

```rust
// In respond_verify_foreign_tx, replace:
let payload_hash: [u8; 32] = response.payload_hash.0;

// With something like:
let expected_payload_hash: [u8; 32] = derive_payload_hash_from_request(&request);
assert_eq!(
    response.payload_hash.0, expected_payload_hash,
    "payload_hash in response does not match the pending request"
);
let payload_hash = expected_payload_hash;
```

This mirrors the correct pattern already used in `respond`, where `payload_hash` is always taken from `request.payload` and never from the node-supplied response.

---

### Proof of Concept

**Setup:** Contract is Running. Alice submits `verify_foreign_transaction` for TX_Y. A pending entry `pending_verify_foreign_tx_requests[request_Y] = [yield_Y]` is created.

**Attack (single Byzantine node N₁):**

1. N₁ participates in the normal MPC signing flow for TX_X and obtains `sig_TX_X` (a valid threshold signature for `hash(TX_X)`).
2. N₁ calls:
   ```
   respond_verify_foreign_tx(
       request = request_Y,           // TX_Y's pending request key
       response = {
           payload_hash: hash(TX_X), // TX_X's hash
           signature: sig_TX_X       // valid sig for TX_X
       }
   )
   ```
3. Contract executes:
   - `assert_caller_is_attested_participant_and_protocol_active()` → passes (N₁ is a participant).
   - `verify_ecdsa_signature(sig_TX_X, hash(TX_X), root_pk)` → passes (signature is valid).
   - `resolve_yields_for(&mut pending_verify_foreign_tx_requests, &request_Y, response_bytes)` → TX_Y's yield is resumed with TX_X's response; TX_Y's map entry is deleted.
4. Alice's callback fires with `payload_hash = hash(TX_X)` and `signature = sig_TX_X`.
5. TX_Y's request is permanently gone. Alice cannot resubmit the same request (the yield has already been consumed).

**Root cause (exact lines):** [1](#0-0) [2](#0-1) 

**Correct pattern (for contrast — `respond` binds payload to the request):** [3](#0-2) 

**`resolve_yields_for` permanently removes the entry:** [4](#0-3)

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

**File:** crates/contract/src/lib.rs (L726-733)
```rust
                let payload_hash: [u8; 32] = response.payload_hash.0;

                // Check the signature is correct against the root public key
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    &payload_hash,
                    &secp_pk,
                )
```

**File:** crates/contract/src/lib.rs (L749-753)
```rust
        pending_requests::resolve_yields_for(
            &mut self.pending_verify_foreign_tx_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
```

**File:** crates/contract/src/pending_requests.rs (L74-87)
```rust
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
```
