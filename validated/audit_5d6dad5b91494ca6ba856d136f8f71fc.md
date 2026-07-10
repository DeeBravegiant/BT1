### Title
Cross-Request Replay of `respond_verify_foreign_tx` Responses via Unbound `payload_hash` — (File: `crates/contract/src/lib.rs`, `crates/node/src/providers/verify_foreign_tx/sign.rs`)

---

### Summary

The `respond_verify_foreign_tx` contract function verifies only that the submitted `response.payload_hash` carries a valid ECDSA signature under the root (un-tweaked) public key. It does **not** verify that `response.payload_hash` is a hash of a `ForeignTxSignPayload` whose embedded `ForeignChainRpcRequest` matches the pending request being resolved. Because `VerifyForeignTransactionRequest` contains no per-instance nonce or timestamp, a single Byzantine MPC participant (strictly below the signing threshold) can replay any previously-issued valid `(payload_hash, signature)` pair to resolve any future pending request that shares the same `ForeignChainRpcRequest` key — including requests submitted after a foreign-chain reorganization — producing forged foreign-chain verification and enabling double-spend conditions.

---

### Finding Description

**Root cause — `respond_verify_foreign_tx` does not bind `payload_hash` to the pending request**

`crates/contract/src/lib.rs` lines 718–734:

```rust
let signature_is_valid = match (&response.signature, public_key) {
    (
        dtos::SignatureResponse::Secp256k1(signature_response),
        PublicKeyExtended::Secp256k1 { near_public_key },
    ) => {
        let secp_pk = ...;
        let payload_hash: [u8; 32] = response.payload_hash.0;   // ← caller-supplied

        // Check the signature is correct against the root public key
        near_mpc_signature_verifier::verify_ecdsa_signature(
            signature_response,
            &payload_hash,   // ← NOT derived from the pending request
            &secp_pk,
        )
        .is_ok()
    }
    ...
};
```

The contract accepts any `(payload_hash, signature)` pair that is cryptographically valid under the root key, regardless of whether `payload_hash` encodes the `ForeignChainRpcRequest` that is actually pending.

**Root cause — `VerifyForeignTransactionRequest` carries no per-instance nonce**

`crates/near-mpc-contract-interface/src/types/foreign_chain.rs` lines 124–128:

```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```

No nonce, receipt-id, or timestamp is included. Two calls to `verify_foreign_transaction` with identical arguments produce the same map key, so a response produced for the first call is structurally indistinguishable from a response for the second call.

**Root cause — node-side signing uses a hardcoded zero tweak**

`crates/node/src/providers/verify_foreign_tx/sign.rs` lines 39–47:

```rust
Ok(SignatureRequest {
    id: request.id,
    receipt_id: request.receipt_id,
    payload: Payload::Ecdsa(payload_bytes),
    tweak: Tweak::new([0u8; 32]),   // ← always the root key
    entropy: request.entropy,
    timestamp_nanosec: request.timestamp_nanosec,
    domain: request.domain_id,
})
```

Because the tweak is always zero, every foreign-tx signature is produced under the same root key. A signature produced for `ForeignTxSignPayloadV1{request: X, values: [block_hash_Y]}` is equally valid as a response to any pending request whose `ForeignChainRpcRequest` key matches `X`.

**Contrast with `respond` for regular sign requests**

`crates/contract/src/lib.rs` lines 597–608 show that `respond` derives the expected public key from the request's tweak (which encodes `predecessor_id` and `path`), binding the signature to the specific caller and path. `respond_verify_foreign_tx` performs no equivalent binding.

---

### Impact Explanation

**Scenario A — same-request replay after chain reorganization (double-spend)**

1. Bridge calls `verify_foreign_transaction(tx_id=X, chain=Bitcoin, confirmations=6, extractors=[BlockHash])`.
2. MPC network honestly signs `H(borsh(ForeignTxSignPayloadV1{request=X, values=[block_hash_Y]}))` → response `R₁`.
3. Bridge receives `R₁`, verifies the signature, and releases funds.