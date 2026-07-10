### Title
`respond_verify_foreign_tx` Does Not Validate `payload_hash` Against the Submitted Request — (File: `crates/contract/src/lib.rs`)

---

### Summary

The `respond_verify_foreign_tx` contract method accepts two caller-supplied parameters — a `VerifyForeignTransactionRequest` (used as the pending-map key) and a `VerifyForeignTransactionResponse` (which contains a `payload_hash` and a `signature`) — but never validates that `response.payload_hash` is the hash that actually corresponds to the supplied `request`. A single Byzantine participant can replay a legitimately-produced MPC signature from a prior foreign-chain verification session against a *different* pending request, causing the waiting user contract to receive a `VerifyForeignTransactionResponse` whose `payload_hash` belongs to a completely different foreign transaction.

---

### Finding Description

In `respond_verify_foreign_tx` the contract performs two independent operations:

1. **Signature check** — verifies that `response.signature` is a valid ECDSA signature over `response.payload_hash` under the domain's *root* public key (no tweak).
2. **Queue resolution** — looks up `request` in `pending_verify_foreign_tx_requests` and delivers `response` (serialised) to every queued yield.

```rust
// crates/contract/src/lib.rs  ~L726-L753
let payload_hash: [u8; 32] = response.payload_hash.0;

// Check the signature is correct against the root public key
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,
)
.is_ok()
// ...
pending_requests::resolve_yields_for(
    &mut self.pending_verify_foreign_tx_requests,
    &request,
    serde_json::to_vec(&response).unwrap(),
)
```

There is **no step that binds `response.payload_hash` to `request`**. The contract never recomputes the expected hash from the request fields and compares it with `response.payload_hash`.

Contrast this with the regular `respond` path, where the payload is taken directly from the stored `request` object and the signature is verified against it — the two parameters are structurally coupled:

```rust
// crates/contract/src/lib.rs  ~L600-L608
let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    payload_hash,
    &expected_public_key,
)
```

For `verify_foreign_transaction`, the hash is computed off-chain by the MPC nodes from the request fields (including the foreign-chain `tx_id`) and the extracted on-chain values:

```rust
// crates/node/src/providers/verify_foreign_tx/sign.rs  ~L34-L47
let payload_hash: [u8; ECDSA_PAYLOAD_SIZE_BYTES] =
    foreign_tx_payload.compute_msg_hash()?.into();
```

Because the contract never re-derives this hash from `request` and compares it with `response.payload_hash`, a caller can supply any `(request, response)` pair where the signature is valid for `response.payload_hash` — regardless of whether that hash has anything to do with `request`.

**Concrete replay attack path (single Byzantine participant):**

1. The MPC network legitimately processes `verify_foreign_transaction` for **transaction A** (tx_id = A). The leader submits `respond_verify_foreign_tx(request_A, response_A)` on-chain. `response_A.payload_hash = H_A` and `response_A.signature = σ_A` (valid ECDSA over `H_A` under the root key). Both values are now permanently visible on-chain.
2. A user later submits `verify_foreign_transaction` for **transaction B** (tx_id = B). The corresponding `request_B` is now pending in the contract map.
3. The malicious participant calls `respond_verify_foreign_tx(request_B, response_A)` — i.e., the legitimate `request_B` paired with the *old* `response_A` (containing `H_A` and `σ_A`).
4. The contract:
   - Finds `request_B` in the pending map ✓
   - Verifies `σ_A` over `H_A` under the root key ✓ (the signature is genuinely valid)
   - Delivers `response_A` (with `payload_hash = H_A`) to the yield waiting for `request_B` ✓
5. The user contract for transaction B receives a `VerifyForeignTransactionResponse` asserting that `H_A` (the hash of **transaction A**) was verified and signed — not the hash of their own transaction B.

---

### Impact Explanation

The user contract waiting on `request_B` receives a cryptographically valid `VerifyForeignTransactionResponse` whose `payload_hash` belongs to a completely different foreign transaction. Any downstream logic that trusts `payload_hash` as proof of what was verified — e.g., releasing bridge funds, crediting a cross-chain transfer, or updating an accounting ledger — will act on the wrong transaction identity. This constitutes **forged foreign-chain verification** and can directly enable **invalid bridge execution or double-spend conditions** (e.g., the same prior signature `σ_A` can be replayed against multiple different pending requests, each time convincing a different user contract that their transaction was verified).

Impact category: **High** — forged foreign-chain verification / light-client-style verification bypass causing invalid bridge execution.

---

### Likelihood Explanation

- The attacker is a **single** attested MPC participant — no threshold collusion is required.
- All on-chain `respond_verify_foreign_tx` calls (including `payload_hash` and `signature`) are permanently public; any participant can harvest prior signatures trivially.
- The attacker only needs to wait for a new `verify_foreign_transaction` request to appear in the pending map, then immediately front-run the honest nodes with the replayed response.
- No special hardware, social engineering, or network-level access is required.

---

### Recommendation

Inside `respond_verify_foreign_tx`, after the signature check, recompute the expected payload hash from the `request` fields and assert it equals `response.payload_hash`:

```rust
// Pseudocode — derive the canonical hash the nodes should have signed
let expected_hash = ForeignTxSignPayload::from_request(&request)
    .compute_msg_hash()?;
if expected_hash != response.payload_hash {
    return Err(RespondError::PayloadHashMismatch.into());
}
```

Alternatively, mirror the design of `respond`: store the expected `payload_hash` in the pending-request map at submission time (computed deterministically from the request) and compare it against `response.payload_hash` in the callback.

---

### Proof of Concept

```
// Step 1 – observe a prior legitimate respond call on-chain:
//   respond_verify_foreign_tx(request_A, { payload_hash: H_A, signature: σ_A })
//   σ_A is valid ECDSA over H_A under the root secp256k1 key.

// Step 2 – a new user submits:
//   verify_foreign_transaction(request_B)   // tx_id = B, now pending in map

// Step 3 – malicious participant calls:
respond_verify_foreign_tx(
    request  = request_B,          // valid pending key → passes map lookup
    response = { payload_hash: H_A, signature: σ_A }  // recycled from step 1
)

// Contract execution:
//   verify_ecdsa_signature(σ_A, H_A, root_pk) → OK   (σ_A is genuinely valid)
//   resolve_yields_for(request_B, serialize({ H_A, σ_A })) → delivers to user B

// User B's contract receives VerifyForeignTransactionResponse { payload_hash: H_A, signature: σ_A }
// and incorrectly concludes that transaction A's hash was the verified payload for their request.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L30-47)
```rust
fn build_signature_request(
    request: &VerifyForeignTxRequest,
    foreign_tx_payload: &dtos::ForeignTxSignPayload,
) -> anyhow::Result<SignatureRequest> {
    let payload_hash: [u8; ECDSA_PAYLOAD_SIZE_BYTES] =
        foreign_tx_payload.compute_msg_hash()?.into();
    let payload_bytes: BoundedVec<u8, ECDSA_PAYLOAD_SIZE_BYTES, ECDSA_PAYLOAD_SIZE_BYTES> =
        payload_hash.into();

    Ok(SignatureRequest {
        id: request.id,
        receipt_id: request.receipt_id,
        payload: Payload::Ecdsa(payload_bytes),
        tweak: Tweak::new([0u8; 32]),
        entropy: request.entropy,
        timestamp_nanosec: request.timestamp_nanosec,
        domain: request.domain_id,
    })
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
