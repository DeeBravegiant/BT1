### Title
`respond_verify_foreign_tx` Does Not Bind `response.payload_hash` to the Submitted `request` — Enabling Single-Node Cross-Request Signature Replay - (File: `crates/contract/src/lib.rs`)

### Summary

`respond_verify_foreign_tx` verifies that the MPC signature in the response is cryptographically valid over the caller-supplied `response.payload_hash`, but it never checks that `response.payload_hash` was actually derived from the `request` that is being resolved. A single Byzantine MPC participant (below the signing threshold) can replay a threshold signature that was legitimately produced for a prior request and use it to resolve a completely different pending `verify_foreign_transaction` request, delivering a forged attestation to the waiting caller.

### Finding Description

The `respond_verify_foreign_tx` method in `crates/contract/src/lib.rs` performs two independent checks and then resolves the pending yield queue:

1. It verifies that `response.signature` is a valid ECDSA signature over `response.payload_hash` using the domain's root public key.
2. It calls `resolve_yields_for(&mut self.pending_verify_foreign_tx_requests, &request, ...)` to drain the yield queue keyed on `request`. [1](#0-0) 

The critical missing step is that the contract never recomputes the expected `payload_hash` from the `request` content and verifies that `response.payload_hash` matches it. The `payload_hash` is defined as `SHA-256(borsh(ForeignTxSignPayload::V1 { request, values }))`: [2](#0-1) 

The `request` struct stored in `pending_verify_foreign_tx_requests` contains the full `ForeignChainRpcRequest` (tx_id, extractors, confirmations, etc.) and `domain_id`, but this content is only used as a map key for queue lookup — it is never cross-checked against the `payload_hash` in the response: [3](#0-2) 

The `resolve_yields_for` helper simply removes the queue entry and resumes all waiting yields with whatever `response_bytes` it is given: [4](#0-3) 

This is directly analogous to the `cancelOrder` pattern: the `request` content is stored in the pending map (analogous to `cancelled[orderHash] = true`) but is never consulted when the response is processed (analogous to the mapping never being checked in `matchOrders`).

### Impact Explanation

A user (e.g., an Omnibridge contract) submits `verify_foreign_transaction` for Bitcoin `tx_id=Y`. The contract stores the pending yield under the key `VerifyForeignTransactionRequest { request: Bitcoin(tx_id=Y), ... }`. A Byzantine MPC participant replays a threshold signature `sig_A` that was legitimately produced by the MPC network for a prior request `tx_id=X`. They call:

```
respond_verify_foreign_tx(
    request = { Bitcoin(tx_id=Y), domain_id, ... },   // matches the pending queue key
    response = { payload_hash = hash_of_X_data, signature = sig_A }
)
```

The contract:
- Confirms `sig_A` is a valid signature over `hash_of_X_data` ✓ (it is — it was legitimately produced)
- Finds a pending yield for `tx_id=Y` ✓
- Resolves the yield with `response = { payload_hash = hash_of_X_data, signature = sig_A }`

The bridge contract receives a `VerifyForeignTransactionResponse` that carries a valid MPC root-key signature, but the attested payload describes `tx_id=X`'s data, not `tx_id=Y`. If the bridge contract does not independently recompute and compare the expected `payload_hash`, it will act on a forged attestation — enabling invalid bridge execution or double-spend conditions.

This matches the allowed impact: **High — forged foreign-chain verification that causes invalid bridge execution.**

### Likelihood Explanation

- The attack requires only a **single** Byzantine MPC participant (below the signing threshold). No threshold collusion is needed.
- The replayed signature is a legitimate threshold signature that was already published on-chain when the prior request was resolved. It is publicly observable by any participant.
- The attacker does not need to forge any cryptographic material; they only need to submit an existing signature with a mismatched `request` key.
- The `respond_verify_foreign_tx` function does not restrict which participant may call it (any attested participant may call it, not just the designated leader for a given request). [5](#0-4) 

### Recommendation

The contract must recompute the expected `payload_hash` from the `request` and the `values` provided in the response, and assert that it matches `response.payload_hash` before resolving the yield. Concretely, `respond_verify_foreign_tx` should accept the `values: Vec<ExtractedValue>` as an explicit parameter, compute `expected_hash = SHA-256(borsh(ForeignTxSignPayload::V1 { request, values }))`, and assert `expected_hash == response.payload_hash` before calling `resolve_yields_for`. This binds the signature to the specific request content, closing the replay window.

### Proof of Concept

1. MPC network legitimately processes request A (`Bitcoin tx_id=X`). The leader calls `respond_verify_foreign_tx(request=A, response={payload_hash=H_A, signature=sig_A})`. `sig_A` and `H_A` are now public on-chain.

2. A user submits `verify_foreign_transaction` for request B (`Bitcoin tx_id=Y`). The contract stores a pending yield under key B.

3. Byzantine participant (single node, below threshold) calls:
   ```
   respond_verify_foreign_tx(
       request = B,                                    // valid pending key
       response = { payload_hash = H_A, sig = sig_A } // replayed from request A
   )
   ```

4. Contract checks: `verify_ecdsa(sig_A, H_A, root_pk)` → valid ✓. Pending yield for B exists ✓. Resolves B with `{payload_hash=H_A, sig=sig_A}`.

5. User's bridge contract receives a response with a valid MPC signature, but `payload_hash` describes `tx_id=X`'s extracted values, not `tx_id=Y`. The bridge processes the wrong transaction. [6](#0-5) [7](#0-6)

### Citations

**File:** crates/contract/src/lib.rs (L691-753)
```rust
    #[handle_result]
    pub fn respond_verify_foreign_tx(
        &mut self,
        request: VerifyForeignTransactionRequest,
        response: VerifyForeignTransactionResponse,
    ) -> Result<(), Error> {
        let signer = Self::assert_caller_is_signer();

        log!(
            "respond_verify_foreign_tx: signer={}, request={:?}",
            &signer,
            &request
        );

        self.assert_caller_is_attested_participant_and_protocol_active();

        if !self.protocol_state.is_running_or_resharing() {
            return Err(InvalidState::ProtocolStateNotRunning.into());
        }

        if !self.accept_requests {
            return Err(TeeError::TeeValidationFailed.into());
        }

        let domain = request.domain_id;
        let public_key = self.public_key_extended(domain.0.into())?;

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

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L124-128)
```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
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
