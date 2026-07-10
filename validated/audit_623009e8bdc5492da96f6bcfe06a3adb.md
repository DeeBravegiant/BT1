### Title
`respond_verify_foreign_tx` Does Not Bind `response.payload_hash` to the Original `request` — Cross-Request Replay Enables Forged Foreign-Chain Verification - (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_verify_foreign_tx` verifies that the MPC signature is valid for the caller-supplied `response.payload_hash`, but never checks that `response.payload_hash` was actually derived from the `request` being resolved. A single malicious attested participant (the signing leader) can replay a legitimately-produced MPC signature for request A as the response to a different pending request B, causing the contract to certify a foreign-chain transaction that was never verified.

---

### Finding Description

The `respond_verify_foreign_tx` function in `crates/contract/src/lib.rs` accepts two independent arguments: a `request: VerifyForeignTransactionRequest` (used to look up and drain the pending yield queue) and a `response: VerifyForeignTransactionResponse` (containing `payload_hash` and `signature`). The only cryptographic check performed is that the signature is valid for the `payload_hash` under the root MPC public key: [1](#0-0) 

The contract never verifies that `response.payload_hash` equals `SHA-256(borsh(ForeignTxSignPayload { request, extracted_values }))` for the specific `request` being resolved. The `payload_hash` is entirely node-supplied and unbound to the `request` argument.

This is structurally identical to the NestedDca bug: in that case, `swapParams.amountOutMinimum` was computed in `performDca` but not passed to `_performDcaSwap`, which re-read from storage (getting 0). Here, the expected `payload_hash` is computable from `request` but is never computed or enforced inside `respond_verify_foreign_tx`; the function only checks the signature against whatever hash the node provides.

By contrast, the regular `respond` function correctly derives the expected payload from the stored request itself: [2](#0-1) 

The correct binding check is implemented in the off-chain SDK helper `ForeignChainSignatureVerifier::verify_signature`, which explicitly checks `expected_payload_hash == response.payload_hash`: [3](#0-2) 

But this check is in the SDK used by callers, not enforced by the contract itself.

The `payload_hash` is constructed by the node from `ForeignTxSignPayload::V1 { request, values }` and signed by the MPC threshold: [4](#0-3) 

The contract stores the `request` in the pending map at submission time: [5](#0-4) 

But when resolving, it never recomputes or checks the expected hash from that stored request.

---

### Impact Explanation

**High — Forged foreign-chain verification / cross-request replay causing invalid bridge execution.**

A malicious leader node can take a legitimately-produced threshold MPC signature for request A (e.g., Bitcoin tx_id `[1;32]`, block_hash `X`) and submit it as the response for a different pending request B (e.g., Bitcoin tx_id `[2;32]`). The contract accepts the call because:

1. The signature is valid for `payload_hash_A` under the root key ✓
2. Request B exists in the pending map ✓
3. No binding check between `payload_hash_A` and request B ✗

Request B's yield is resolved with `{payload_hash: payload_hash_A, signature: sig_A}`. The bridge service receives a contract-certified response claiming that transaction `[2;32]` was verified, but the actual MPC-signed payload corresponds to transaction `[1;32]`. Any bridge that trusts the contract's yield-resume response without independently re-running `ForeignChainSignatureVerifier::verify_signature` will accept a forged verification, potentially enabling double-spend or invalid bridge execution.

---

### Likelihood Explanation

**Medium.** The attack requires:
1. Two distinct `verify_foreign_transaction` requests to be pending simultaneously — normal in any active bridge.
2. A valid threshold MPC signature to have been produced for one of them — this happens in the normal course of operation.
3. A single malicious attested participant acting as the signing leader to submit the mismatched response — one Byzantine node below threshold suffices.

No threshold-level collusion is needed. The malicious node simply intercepts the legitimately-computed response for request A and submits it against request B before submitting the correct response for A.

---

### Recommendation

Inside `respond_verify_foreign_tx`, recompute the expected `payload_hash` from the `request` and the extracted values embedded in the response, and assert equality before accepting the signature. Concretely, the contract should reconstruct `ForeignTxSignPayload::V1 { request: request.request.clone(), values: response.extracted_values }` (if extracted values are included in the response), compute its `msg_hash`, and verify `computed_hash == response.payload_hash` before calling `resolve_yields_for`. Alternatively, include the `payload_hash` in the stored pending-request key so that a mismatched hash cannot resolve the correct queue entry.

---

### Proof of Concept

**Setup:** Two pending requests exist:
- Request A: `{ domain_id: D, request: Bitcoin { tx_id: [1;32], ... } }`
- Request B: `{ domain_id: D, request: Bitcoin { tx_id: [2;32], ... } }`

**Step 1:** Threshold nodes legitimately compute and sign `payload_hash_A = SHA-256(borsh(ForeignTxSignPayload::V1 { request: A.request, values: [BlockHash([42;32])] }))`.

**Step 2:** Malicious leader calls:
```
respond_verify_foreign_tx(
    request = Request_B,
    response = { payload_hash: payload_hash_A, signature: sig_A }
)
```

**Step 3:** Contract checks:
- `verify_ecdsa_signature(sig_A, payload_hash_A, root_pk)` → **valid** ✓
- `resolve_yields_for(pending_verify_foreign_tx_requests, Request_B, response)` → **drains Request B's queue** ✓

**Step 4:** Bridge service for Request B receives `{ payload_hash: payload_hash_A, signature: sig_A }` — a response that cryptographically certifies transaction `[1;32]` but is delivered as the answer for transaction `[2;32]`. [6](#0-5) [7](#0-6)

### Citations

**File:** crates/contract/src/lib.rs (L549-556)
```rust
        let request = args_into_verify_foreign_tx_request(request);
        let callback_args = serde_json::to_vec(&(&request,)).unwrap();
        self.enqueue_yield_request(
            method_names::RETURN_VERIFY_FOREIGN_TX_AND_CLEAN_STATE_ON_SUCCESS,
            callback_args,
            callback_gas,
            move |this, id| this.add_verify_foreign_tx_request(request, id),
        );
```

**File:** crates/contract/src/lib.rs (L597-608)
```rust
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

**File:** crates/node/src/mpc_client.rs (L595-601)
```rust
                                        let payload_hash = response.0.0.compute_msg_hash()?;
                                        let response = contract_args::VerifyForeignTransactionRespondArgs::from_signature(
                                            verify_foreign_tx_attempt.request.clone(),
                                            payload_hash,
                                            response.0.1,
                                            response.1,
                                        )?;
```
