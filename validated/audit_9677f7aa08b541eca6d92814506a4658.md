### Title
`respond_verify_foreign_tx` Does Not Bind `response.payload_hash` to the Submitted `request` — Forged Foreign-Chain Verification by a Single Byzantine Node - (File: crates/contract/src/lib.rs)

### Summary

`respond_verify_foreign_tx` verifies that the submitted ECDSA signature is valid over `response.payload_hash`, but never checks that `response.payload_hash` was actually derived from the `request` argument used to look up and drain the pending yield queue. A single attested MPC participant (below the signing threshold) that previously led a signing round for any request R2 can reuse the resulting threshold signature to resolve a completely different pending request R1 with a mismatched payload hash, delivering a forged foreign-chain verification response to the waiting caller.

### Finding Description

`respond_verify_foreign_tx` in `crates/contract/src/lib.rs` accepts two independent arguments:

- `request: VerifyForeignTransactionRequest` — used as the map key to locate and drain the pending yield queue.
- `response: VerifyForeignTransactionResponse` — contains `payload_hash` and `signature`.

The only cryptographic check performed is:

```rust
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,   // response.payload_hash — caller-supplied
    &secp_pk,        // root public key
)
.is_ok()
``` [1](#0-0) 

After this check passes, the function immediately resolves every yield queued under `request` with the raw `response` bytes:

```rust
pending_requests::resolve_yields_for(
    &mut self.pending_verify_foreign_tx_requests,
    &request,
    serde_json::to_vec(&response).unwrap(),
)
``` [2](#0-1) 

There is no step that reconstructs the expected `payload_hash` from `request` and compares it to `response.payload_hash`. The contract therefore accepts any valid threshold signature over any 32-byte hash as a legitimate response to any pending request.

The correct payload hash for a request is `SHA-256(borsh(ForeignTxSignPayload{request, extracted_values}))`: [3](#0-2) 

Because `extracted_values` are determined off-chain by the MPC nodes, the contract cannot recompute the hash itself — but it can require the node to submit the full `ForeignTxSignPayload` and verify that `payload.request == request.request` before accepting the response.

The `near-mpc-sdk` helper `ForeignChainSignatureVerifier::verify_signature` does perform this check client-side:

```rust
let payload_is_correct = expected_payload_hash == response.payload_hash;
if !payload_is_correct {
    return Err(VerifyForeignChainError::IncorrectPayloadSigned { ... });
}
``` [4](#0-3) 

However, this check is in an off-chain SDK used by bridge contracts, not enforced by the on-chain MPC contract itself. Bridge contracts that do not use the SDK, or that use it incorrectly, receive no on-chain protection.

### Impact Explanation

A single Byzantine attested MPC node that was previously the leader for request R2 holds the complete threshold signature `sig_H2` over `H2 = hash(R2, values_R2)`. It can call:

```
respond_verify_foreign_tx(
    request = R1,                          // pending request for a different tx
    response = { payload_hash: H2,
                 signature:    sig_H2 }    // valid sig, but for R2's payload
)
```

The contract verifies `sig_H2` over `H2` against the root public key — this passes. It then drains the yield queue for R1 and delivers `{ payload_hash: H2, signature: sig_H2 }` to every caller waiting on R1.

Consequences:

1. **Request lifecycle corruption**: R1's pending yield is permanently consumed. The original caller cannot receive a correct response; they must resubmit and pay the deposit again.
2. **Forged foreign-chain verification**: The caller's bridge contract receives a `VerifyForeignTransactionResponse` whose `payload_hash` encodes a different transaction (R2's tx_id, block hash, extracted values). Any bridge contract that does not independently reconstruct and compare the expected payload hash will treat this as proof that R1's foreign transaction was verified, enabling invalid bridge execution (e.g., crediting a large deposit when only a small one was actually confirmed on the foreign chain).

This maps directly to the allowed High impact: *"forged foreign-chain verification … that causes invalid bridge execution or double-spend conditions."*

### Likelihood Explanation

The attacker must be a single attested MPC participant. No threshold collusion is required. The node only needs to have been the leader for any prior `verify_foreign_transaction` round — a routine occurrence — to possess a reusable threshold signature. The attack is a single on-chain transaction and leaves no on-chain trace distinguishing it from a legitimate response.

### Recommendation

Require the responding node to submit the full `ForeignTxSignPayload` (including `extracted_values`) alongside the response, and enforce on-chain that:

1. `payload.request == request.request` (the payload was built from the correct RPC request)
2. `payload.compute_msg_hash() == response.payload_hash` (the hash is self-consistent)
3. The signature verifies over `response.payload_hash` (existing check)

```diff
 pub fn respond_verify_foreign_tx(
     &mut self,
     request: VerifyForeignTransactionRequest,
+    payload: ForeignTxSignPayload,
     response: VerifyForeignTransactionResponse,
 ) -> Result<(), Error> {
     ...
+    // Bind the response payload to the submitted request.
+    require!(
+        payload.request() == &request.request,
+        "payload request does not match submitted request"
+    );
+    let expected_hash = payload.compute_msg_hash()
+        .map_err(|_| RespondError::PayloadHashComputationFailed)?;
+    require!(
+        expected_hash == response.payload_hash,
+        "payload_hash does not match computed hash"
+    );
     // existing signature check
     ...
 }
```

### Proof of Concept

1. User A submits `verify_foreign_transaction` for Bitcoin tx T1 (e.g., a 10 BTC deposit). The contract queues a yield for request R1 and stores it in `pending_verify_foreign_tx_requests`.

2. In a prior block, the Byzantine node was the leader for a different request R2 (Bitcoin tx T2, a 0.001 BTC deposit). It holds the full threshold signature `sig_H2` over `H2 = SHA-256(borsh(ForeignTxSignPayload{R2, [BlockHash(B2)]}))`.

3. The Byzantine node calls:
   ```
   respond_verify_foreign_tx(
       request  = R1,
       response = { payload_hash: H2, signature: sig_H2 }
   )
   ```

4. The contract at line 729 verifies `sig_H2` over `H2` against the root public key — passes. [5](#0-4) 

5. `resolve_yields_for` drains R1's yield queue and resumes every waiting promise with `{ payload_hash: H2, signature: sig_H2 }`. [6](#0-5) 

6. User A's bridge contract receives a response whose `payload_hash` encodes T2 (0.001 BTC), not T1 (10 BTC). A bridge contract that skips the SDK's `payload_is_correct` check credits User A with 10 BTC worth of wrapped tokens despite only 0.001 BTC being confirmed on-chain.

### Citations

**File:** crates/contract/src/lib.rs (L726-734)
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

**File:** crates/contract/src/lib.rs (L749-753)
```rust
        pending_requests::resolve_yields_for(
            &mut self.pending_verify_foreign_tx_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
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

**File:** crates/near-mpc-sdk/src/foreign_chain.rs (L57-63)
```rust
        let payload_is_correct = expected_payload_hash == response.payload_hash;

        if !payload_is_correct {
            return Err(VerifyForeignChainError::IncorrectPayloadSigned {
                got: response.payload_hash.clone(),
                expected: expected_payload_hash,
            });
```

**File:** crates/contract/src/pending_requests.rs (L74-81)
```rust
    let resumed = requests
        .remove(request)
        .unwrap_or_default()
        .into_iter()
        .map(|YieldIndex { data_id }| {
            env::promise_yield_resume(&data_id, response_bytes.clone());
        })
        .count();
```
