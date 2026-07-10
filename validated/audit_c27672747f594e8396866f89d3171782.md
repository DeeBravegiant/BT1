### Title
Missing Validation of `response.payload_hash` Against Request Transaction in `respond_verify_foreign_tx` — (`File: crates/contract/src/lib.rs`)

---

### Summary

`respond_verify_foreign_tx` verifies that the submitted signature is cryptographically valid for `response.payload_hash`, but never checks that `response.payload_hash` was correctly derived from the transaction ID contained in the `request`. A malicious attested leader node can replay a valid signature obtained from a prior legitimate signing session, delivering a signature for an entirely different payload to the waiting caller.

---

### Finding Description

The vulnerability class from the external report is **insufficient state/invariant validation during a critical execution step**: the `transfer_nft` function checked that a bid existed but never verified the bid amount matched the listed price, allowing a zero-payment transfer. The direct analog here is that `respond_verify_foreign_tx` checks that the submitted signature is valid for `response.payload_hash`, but never verifies that `response.payload_hash` is the hash that should have been derived from the foreign transaction in `request`.

In `respond_verify_foreign_tx`, the contract:

1. Asserts the caller is an attested participant.
2. Extracts `payload_hash` **from the response** (caller-supplied).
3. Verifies the signature against `payload_hash` and the **root public key**.
4. Resolves the pending yield keyed on `request` with the full `response`. [1](#0-0) 

Critically, step 3 only proves "this signature is valid for this `payload_hash`." It does **not** prove that `payload_hash == hash(request.tx_id)`. The `request` object is used solely as a lookup key into `pending_verify_foreign_tx_requests`; its transaction-ID content is never compared against the response's `payload_hash`. [2](#0-1) 

Contrast this with `respond`, where the payload is embedded inside the `SignatureRequest` key itself, so the signature is necessarily verified against the exact payload the user submitted: [3](#0-2) 

---

### Impact Explanation

A malicious leader node can deliver a signature for an **arbitrary foreign-chain payload** to a user who requested verification of a specific transaction. The user's yield resumes with `response` containing the attacker-chosen `payload_hash` and a valid signature for it. The user (or the bridge contract consuming the result) receives what appears to be a legitimately MPC-signed verification of a transaction they never submitted, enabling **forged foreign-chain verification** and potential double-spend conditions.

This matches the allowed impact: *"High. Cross-chain replay, forged foreign-chain verification … that causes invalid bridge execution or double-spend conditions."*

---

### Likelihood Explanation

The attacker must be a single malicious **attested participant** acting as the signing leader. They do not need threshold-or-above collusion. The attack requires a valid signature for the target `payload_hash` under the root key. This can be obtained by:

- Participating honestly in a prior legitimate `verify_foreign_transaction` signing session for a different transaction whose hash the attacker wants to reuse, then replaying that signature against a new request.
- Or, since `verify_foreign_transaction` signs with the **root key** (no path derivation), any prior root-key signature for the same 32-byte value is reusable. [4](#0-3) 

The `pending_requests::resolve_yields_for` call will succeed as long as the `request` key exists in the map, regardless of whether the response content matches the request's intent. [5](#0-4) 

---

### Recommendation

After verifying the signature, add an explicit check that `response.payload_hash` equals the hash that the contract itself derives from the transaction ID embedded in `request`. The contract already has access to the full `VerifyForeignTransactionRequest`; it should compute the expected payload hash from `request.chain()` / `request.tx_id` and assert equality with `response.payload_hash` before resolving the yield. This mirrors how `respond` embeds the payload inside the `SignatureRequest` key, making substitution structurally impossible.

---

### Proof of Concept

1. Honest user A calls `verify_foreign_transaction` for `tx_id_A`. The contract stores a pending yield keyed on `request_A`.
2. Honest user B calls `verify_foreign_transaction` for `tx_id_B`. The contract stores a pending yield keyed on `request_B`.
3. The MPC network runs the threshold protocol for `request_A`, producing `sig_A` over `hash(tx_id_A)` under the root key. The malicious leader receives `sig_A`.
4. The malicious leader calls `respond_verify_foreign_tx(request_B, {payload_hash: hash(tx_id_A), signature: sig_A})`.
5. The contract verifies `sig_A` is valid for `hash(tx_id_A)` against the root key — **passes**.
6. The contract resolves user B's yield with `{payload_hash: hash(tx_id_A), signature: sig_A}`.
7. User B's callback receives a valid MPC signature for `tx_id_A`, not `tx_id_B`. If user B's bridge contract trusts this result, it executes the wrong (or attacker-chosen) foreign-chain action.

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

**File:** crates/contract/src/lib.rs (L692-754)
```rust
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
