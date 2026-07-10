### Title
`respond_verify_foreign_tx` Does Not Bind `response.payload_hash` to the Submitted `request` — Cross-Request Signature Replay by a Byzantine Participant - (File: `crates/contract/src/lib.rs`)

### Summary

`respond_verify_foreign_tx` verifies that the submitted signature is cryptographically valid over the caller-supplied `response.payload_hash`, but never checks that `payload_hash` is the correct hash for the accompanying `request`. A single Byzantine attested participant (below the signing threshold) who is the leader for one pending request can replay that request's valid threshold signature as the response to a *different* pending request, causing the second request to be resolved with fabricated verification data.

### Finding Description

The `respond_verify_foreign_tx` function in `crates/contract/src/lib.rs` performs three checks before resolving pending yields:

1. The caller is an attested participant (`assert_caller_is_attested_participant_and_protocol_active`).
2. The protocol is in `Running` or `Resharing` state.
3. The submitted `response.signature` is a valid ECDSA signature over `response.payload_hash` under the **root** public key. [1](#0-0) 

What is **never** checked is whether `response.payload_hash` is the correct hash for the supplied `request`. The contract simply trusts whatever `payload_hash` the caller provides, verifies the signature over it, and then resolves all queued yields for `request` with the full `response` blob: [2](#0-1) 

The correct `payload_hash` for a foreign-tx request is `hash(ForeignTxSignPayloadV1 { request, values })`, where `values` are the data extracted from the foreign chain. The contract has access to `request` but not `values`, so it cannot independently recompute the expected hash — but it also makes no attempt to verify even the `request`-derived portion of the hash.

The `resolve_yields_for` helper drains **all** queued yields for the given request key in one call, serialising the full `response` (including the wrong `payload_hash`) into every waiting promise: [3](#0-2) 

### Impact Explanation

A Byzantine attested participant who is the designated leader for request **R1** learns the full threshold signature `(H1, S1)` during the off-chain MPC signing round before submitting it on-chain. While **R2** (a different `verify_foreign_transaction` request) is simultaneously pending, the attacker calls:

```
respond_verify_foreign_tx(R2, { payload_hash: H1, signature: S1 })
```

The contract accepts this call because:
- The caller is an attested participant ✓
- `S1` is a valid ECDSA signature over `H1` under the root key ✓
- `R2` exists in `pending_verify_foreign_tx_requests` ✓

All yields queued under `R2` are drained and resolved with `{ payload_hash: H1, signature: S1 }`. The users who submitted `R2` receive a `VerifyForeignTransactionResponse` whose `payload_hash` corresponds to a completely different foreign transaction. Any bridge or application that uses this response to authorise an on-chain action (e.g., releasing funds, minting tokens) will do so based on fabricated verification data, enabling double-spend or invalid bridge execution. Because `R2` is removed from the pending map, the legitimate response can never be delivered; affected users must re

### Citations

**File:** crates/contract/src/lib.rs (L718-754)
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
