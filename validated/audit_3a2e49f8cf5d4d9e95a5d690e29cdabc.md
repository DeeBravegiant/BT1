### Title
Missing Payload-Hash-to-Request Binding in `respond_verify_foreign_tx` Enables Cross-Request Response Substitution — (File: `crates/contract/src/lib.rs`)

### Summary

The `respond_verify_foreign_tx` function verifies that the submitted ECDSA signature is valid for the caller-supplied `response.payload_hash`, but never verifies that `response.payload_hash` actually encodes the original `request.request` (`ForeignChainRpcRequest`). A single malicious attested MPC node that legitimately leads a signing session for request B can reuse the resulting threshold signature to resolve a completely different pending request A, delivering a response whose `payload_hash` encodes foreign-chain data for the wrong transaction. This check is present in the analogous `respond()` function but is absent in `respond_verify_foreign_tx()`.

### Finding Description

**Root cause — missing binding check in `respond_verify_foreign_tx`**

In `respond()` the payload hash is read from the *stored* request, not from the caller:

```rust
// crates/contract/src/lib.rs:600
let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");
```

The contract therefore knows exactly what was signed and verifies the signature against that known hash.

In `respond_verify_foreign_tx()` the payload hash is taken entirely from the *response* submitted by the node:

```rust
// crates/contract/src/lib.rs:726
let payload_hash: [u8; 32] = response.payload_hash.0;

// Check the signature is correct against the root public key
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,
)
.is_ok()
``` [1](#0-0) 

The contract then resolves the pending request keyed by `request` (the `VerifyForeignTransactionRequest` containing the original `ForeignChainRpcRequest`):

```rust
// crates/contract/src/lib.rs:749-753
pending_requests::resolve_yields_for(
    &mut self.pending_verify_foreign_tx_requests,
    &request,
    serde_json::to_vec(&response).unwrap(),
)
``` [2](#0-1) 

There is **no check** that `response.payload_hash` is the SHA-256 of a `ForeignTxSignPayload::V1` whose embedded `ForeignChainRpcRequest` matches `request.request`.

**What the payload hash encodes**

The node-side code computes:

```rust
// crates/node/src/providers/verify_foreign_tx/sign.rs:34-35
let payload_hash: [u8; ECDSA_PAYLOAD_SIZE_BYTES] =
    foreign_tx_payload.compute_msg_hash()?.into();
``` [3](#0-2) 

`compute_msg_hash` is `SHA-256(borsh(ForeignTxSignPayload::V1 { request: ForeignChainRpcRequest, values: Vec<ExtractedValue> }))`. [4](#0-3) 

The `ForeignChainRpcRequest` (e.g., Bitcoin tx-id, confirmation count, extractors) is baked into the hash. The contract never checks that the `ForeignChainRpcRequest` inside the submitted `payload_hash` matches the `ForeignChainRpcRequest` stored in the pending map.

**Exploit path (single malicious attested node, below threshold)**

1. Malicious node M is an attested participant. It becomes the signing leader for pending request B (`ForeignChainRpcRequest` for Bitcoin tx Y).
2. The threshold signing protocol completes honestly. M now holds the full threshold ECDSA signature `sig_B` over `payload_hash_B = SHA-256(borsh(ForeignTxSignPayload::V1 { request: Bitcoin_tx_Y, values: [...] }))`.
3. A separate pending request A (`ForeignChainRpcRequest` for Bitcoin tx X) exists in `pending_verify_foreign_tx_requests`.
4. M calls `respond_verify_foreign_tx(request_A, VerifyForeignTransactionResponse { payload_hash: payload_hash_B, signature: sig_B })`.
5. The contract:
   - Confirms M is an attested participant ✓
   - Retrieves the root public key for the ForeignTx domain ✓
   - Verifies `sig_B` against `payload_hash_B` — **passes** because `sig_B` is a valid threshold signature ✓
   - Resolves request A with the response for Bitcoin tx Y ✓
6. Request A is consumed. Every caller waiting on request A receives `{ payload_hash: payload_hash_B, signature: sig_B }` — data that corresponds to Bitcoin tx Y, not Bitcoin tx X.

The node uses a zero tweak for foreign-tx signing (confirmed in `build_signature_request` at line 43: `tweak: Tweak::new([0u8; 32])`), so the signature is over the root ForeignTx domain key. The contract also verifies against the root key (no `derive_key_secp256k1` call), making the signature portable across any pending request in the same domain.

<cite repo="Tylerpinwa/mpc--008" path="crates/node/src/providers/verify_foreign_tx/sign.rs" start="39" end

### Citations

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

**File:** crates/contract/src/lib.rs (L749-753)
```rust
        pending_requests::resolve_yields_for(
            &mut self.pending_verify_foreign_tx_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
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

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L1504-1509)
```rust
impl ForeignTxSignPayload {
    pub fn compute_msg_hash(&self) -> std::io::Result<Hash256> {
        let mut hasher = sha2::Sha256::new();
        borsh::BorshSerialize::serialize(self, &mut hasher)?;
        Ok(Hash256(hasher.finalize().into()))
    }
```
