Looking at the core of this question, I need to verify whether `respond_verify_foreign_tx` enforces any binding between the submitted `payload_hash` and the `request` being responded to.

### Title
Missing `payload_hash`-to-`request` Binding in `respond_verify_foreign_tx` Allows Cross-Request Response Replay — (`crates/contract/src/lib.rs`)

---

### Summary

`respond_verify_foreign_tx` verifies only that the submitted signature is valid over the submitted `payload_hash`. It does **not** verify that `payload_hash` is the canonical `SHA-256(borsh(ForeignTxSignPayload{request, values}))` for the specific `request` being resolved. A single Byzantine attested participant can therefore take a legitimately threshold-produced `(H1, sig1)` for request R1 (Bitcoin tx T1) and replay it verbatim as the response for a concurrently pending request R2 (Bitcoin tx T2). Both `resolve_yields_for` calls succeed; both callers receive the same `{payload_hash: H1, signature: sig1}`.

---

### Finding Description

**Entrypoint**: `respond_verify_foreign_tx` in `crates/contract/src/lib.rs`.

The function performs two checks before resolving pending yields:

1. The caller is an attested participant.
2. `verify_ecdsa_signature(sig, payload_hash, root_pk)` passes. [1](#0-0) 

Neither check binds `payload_hash` to the `request` argument. The contract never computes or compares `SHA-256(borsh(ForeignTxSignPayload{request, values}))` against `response.payload_hash`.

After the signature check passes, `resolve_yields_for` is called with the raw `request` as the map key and the serialised `response` (containing the unchecked `payload_hash`) as the resume payload: [2](#0-1) 

`resolve_yields_for` simply removes the entry keyed by `request` and resumes every queued yield with `response_bytes` — it has no knowledge of whether the hash inside those bytes corresponds to the request: [3](#0-2) 

The canonical hash that *should* bind the response to the request is defined in `ForeignTxSignPayloadV1`, which embeds the full `ForeignChainRpcRequest` (including `tx_id`) and the extracted values: [4](#0-3) 

Because the contract never recomputes this hash from the `request` it is resolving, the binding is entirely absent at the contract layer.

---

### Impact Explanation

**Concrete replay path (single Byzantine attested participant, no threshold collusion required):**

1. R1 (for T1, `tx_id=[0x01;32]`) and R2 (for T2, `tx_id=[0x02;32]`) are both pending in `pending_verify_foreign_tx_requests` under distinct map keys.
2. The threshold MPC protocol legitimately produces `(H1, sig1)` for R1 — `H1 = SHA-256(borsh(ForeignTxSignPayload{request: T1_request, values: [block_hash_of_T1]}))`.
3. The Byzantine node calls `respond_verify_foreign_tx(R1, {payload_hash: H1, sig: sig1})` — succeeds, R1 resolved.
4. The Byzantine node immediately calls `respond_verify_foreign_tx(R2, {payload_hash: H1, sig: sig1})` — **also succeeds** because `sig1` is still a valid ECDSA signature over `H1` under the root key, and R2 exists in the pending map. No binding check rejects it.

R2's caller receives `{payload_hash: H1, signature: sig1}` — a response whose `payload_hash` encodes T1's transaction data, not T2's.

**Bridge-level consequence**: A bridge contract that verifies only `verify_signature(payload_hash, sig, mpc_key)` without reconstructing the expected hash for T2 would accept this as proof that T2 was observed, enabling double-spend or invalid bridge execution. The docs note that callers *should* reconstruct the hash locally, but the MPC contract itself provides no enforcement of this invariant. [5](#0-4) 

---

### Likelihood Explanation

- Requires a single Byzantine TEE-attested participant — below the signing threshold.
- No cryptographic forgery needed; the attacker replays a legitimately produced threshold signature.
- The window is any moment when two distinct foreign-tx requests are concurrently pending (common in production).
- Exploitability at the bridge level depends on whether the bridge contract verifies the hash content; the MPC contract provides no backstop.

---

### Recommendation

Inside `respond_verify_foreign_tx`, after the signature check, require the responder to also supply the `ExtractedValue` list, recompute `expected_hash = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 { request: request.request.clone(), values }).compute_msg_hash()`, and assert `expected_hash == response.payload_hash`. This makes the contract the authoritative enforcer of the binding rather than delegating it entirely to downstream bridge contracts.

Alternatively, if supplying extracted values on-chain is undesirable, the contract could at minimum verify that `payload_hash` is not being reused across distinct request keys by maintaining a short-lived set of recently consumed hashes.

---

### Proof of Concept

```rust
// Unit test sketch (no threshold collusion needed — uses a pre-produced (H1, sig1))
fn respond_verify_foreign_tx__replay_same_response_for_different_request() {
    // Setup: two distinct Bitcoin requests pending
    let request_1 = make_request(tx_id=[0x01;32]);
    let request_2 = make_request(tx_id=[0x02;32]);
    contract.verify_foreign_transaction(request_1.clone());
    contract.verify_foreign_transaction(request_2.clone());

    // Produce a legitimate (H1, sig1) for request_1
    let payload = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
        request: request_1.request.clone(),
        values: vec![ExtractedValue::BitcoinExtractedValue(
            BitcoinExtractedValue::BlockHash([0xAA; 32].into()),
        )],
    });
    let h1 = payload.compute_msg_hash().unwrap();
    let sig1 = sign_with_root_key(&h1);
    let response = VerifyForeignTransactionResponse { payload_hash: h1, signature: sig1 };

    // Resolve request_1 legitimately
    contract.respond_verify_foreign_tx(request_1, response.clone()).unwrap();

    // Replay the SAME response for request_2 — contract accepts it
    // because it only checks sig validity, not hash-to-request binding
    let result = contract.respond_verify_foreign_tx(request_2, response.clone());
    assert!(result.is_ok(), "contract accepted cross-request replay: {result:?}");
    // Both callers now hold {payload_hash: H1, sig: sig1} where H1 encodes T1, not T2
}
``` [6](#0-5) [4](#0-3)

### Citations

**File:** crates/contract/src/lib.rs (L691-754)
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
