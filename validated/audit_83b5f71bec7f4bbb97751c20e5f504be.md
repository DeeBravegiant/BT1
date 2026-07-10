### Title
Single Byzantine Node Can Deliver a Forged Foreign-Chain Verification Result via Unbound `payload_hash` in `respond_verify_foreign_tx` - (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_verify_foreign_tx` verifies only that `response.signature` is a valid root-key signature over `response.payload_hash`. It does **not** verify that `payload_hash` was actually derived from the `request` argument used to look up the pending yield. Because the foreign-tx domain always signs with a **zero tweak** (root key), every valid `(payload_hash, signature)` pair produced by any foreign-tx signing session is cryptographically acceptable as a response to any other pending foreign-tx request. A single Byzantine MPC node (below the signing threshold) can therefore race-submit a signature it legitimately obtained from signing session B as the response to a completely different pending request A, causing the contract to permanently resolve request A with a forged `payload_hash`.

---

### Finding Description

**Root cause — missing payload-to-request binding in `respond_verify_foreign_tx`**

`respond_verify_foreign_tx` (lib.rs:691–754) performs three checks:

1. Caller is an attested participant.
2. `response.signature` is a valid ECDSA signature over `response.payload_hash` under the **root** public key (no tweak applied).
3. Resolves all pending yields keyed by `request` with the serialised `response`. [1](#0-0) 

The critical missing check: the contract never verifies that

```
response.payload_hash == SHA-256(borsh(ForeignTxSignPayload { request, values }))
```

for any `values`. The `payload_hash` field is therefore completely unbound from the `request` argument.

**Contrast with `respond`**

The regular `respond` function derives the expected public key from `request.tweak` before verifying the signature, which cryptographically binds the signature to the specific `(predecessor, path)` pair embedded in the request: [2](#0-1) 

No such binding exists in `respond_verify_foreign_tx`.

**Zero-tweak on the node side**

The MPC node builds every foreign-tx signing request with `tweak: Tweak::new([0u8; 32])`: [3](#0-2) 

This means every foreign-tx signature is produced under the **same** root key. Any `(payload_hash, signature)` pair from any completed foreign-tx session is therefore a valid input to `respond_verify_foreign_tx` for any other pending foreign-tx request.

**`VerifyForeignTransactionRequest` carries no caller identity**

The pending-request map key contains only `{request, domain_id, payload_version}` — no `predecessor_id`: [4](#0-3) 

`ForeignTxSignPayload` likewise contains no caller identity: [5](#0-4) 

**Attack path**

1. Alice calls `verify_foreign_transaction(request_A)` — yield queued in `pending_verify_foreign_tx_requests`.
2. Bob calls `verify_foreign_transaction(request_B)` — yield queued separately.
3. MPC nodes run the signing protocol for request B; Byzantine node participates honestly and obtains `signature_B` over `SHA-256(borsh(ForeignTxSignPayload{request_B, values_B}))`.
4. Byzantine node immediately calls:
   ```
   respond_verify_foreign_tx(
       request  = request_A,          // Alice's pending request
       response = { payload_hash: SHA-256(borsh(ForeignTxSignPayload{request_B, values_B})),
                    signature:    signature_B }
   )
   ```
5. Contract checks: `verify_ecdsa_signature(signature_B, payload_hash, root_key)` → **passes** (signature is genuinely valid).
6. Contract calls `resolve_yields_for(request_A, response)` — Alice's yield is resumed with the wrong `payload_hash`.
7. Honest nodes later attempt `respond_verify_foreign_tx(request_A, correct_response)` → `RequestNotFound` (already drained). [6](#0-5) [7](#0-6) 

---

### Impact Explanation

Alice's bridge contract receives a `VerifyForeignTransactionResponse` whose `payload_hash` corresponds to Bob's transaction B, not Alice's transaction A. Any bridge contract that does not correctly invoke `Foreign

### Citations

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

**File:** crates/contract/src/lib.rs (L746-753)
```rust
            return Err(RespondError::InvalidSignature.into());
        }

        pending_requests::resolve_yields_for(
            &mut self.pending_verify_foreign_tx_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
```

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L39-47)
```rust
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

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L124-128)
```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
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
