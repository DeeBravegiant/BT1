### Title
Cross-Contract Replay of `ForeignTxSignPayload` Signatures Due to Missing Contract Address Binding - (File: `crates/near-mpc-contract-interface/src/types/foreign_chain.rs`)

### Summary

`ForeignTxSignPayload::compute_msg_hash()` produces a signed digest that contains only the foreign-chain request and extracted values. It omits the NEAR contract account ID (the verifying address) and the `domain_id`. A valid `VerifyForeignTransactionResponse` produced for one MPC contract deployment can therefore be replayed verbatim on any other deployment that shares the same MPC root key, satisfying `respond_verify_foreign_tx`'s signature check and resolving a pending yield for a different caller.

### Finding Description

`ForeignTxSignPayload::compute_msg_hash()` is defined as:

```rust
pub fn compute_msg_hash(&self) -> std::io::Result<Hash256> {
    let mut hasher = sha2::Sha256::new();
    borsh::BorshSerialize::serialize(self, &mut hasher)?;
    Ok(Hash256(hasher.finalize().into()))
}
``` [1](#0-0) 

The serialized struct is `ForeignTxSignPayloadV1 { request: ForeignChainRpcRequest, values: Vec<ExtractedValue> }`: [2](#0-1) 

Neither the NEAR contract account ID nor the `domain_id` is included in the signed digest. The node-side `build_signature_request` further hardcodes the tweak to all-zeros, meaning the **root key** (not a derived key) signs the payload:

```rust
Ok(SignatureRequest {
    ...
    tweak: Tweak::new([0u8; 32]),   // zero tweak → root key
    domain: request.domain_id,
})
``` [3](#0-2) 

On the contract side, `respond_verify_foreign_tx` verifies the signature against the **root** public key of the domain (no tweak applied), using only `response.payload_hash` — which is the caller-supplied hash, not one recomputed from the stored request:

```rust
let payload_hash: [u8; 32] = response.payload_hash.0;
// Check the signature is correct against the root public key
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,
)
``` [4](#0-3) 

Because the signed message is `SHA-256(borsh(request, values))` with no contract-address or domain binding, any two contract deployments that share the same MPC root key will accept each other's `VerifyForeignTransactionResponse` for the same foreign transaction.

### Impact Explanation

An attacker who observes a legitimately produced `VerifyForeignTransactionResponse` on one MPC contract deployment (the response is returned on-chain and is publicly visible) can submit it to `respond_verify_foreign_tx` on a second deployment that:

1. Has a pending `verify_foreign_transaction` request for the same foreign-chain transaction, and
2. Uses the same MPC root key.

The second contract will accept the replayed response, resolve the pending yield, and return the attacker-supplied `VerifyForeignTransactionResponse` to the waiting bridge contract. This constitutes **forged foreign-chain verification** and can enable **invalid bridge execution or double-spend conditions** — for example, crediting an inbound Omnibridge transfer twice across two parallel bridge deployments backed by the same MPC network.

This maps to the **High** allowed impact: *"Cross-chain replay, forged foreign-chain verification, light-client-style verification bypass, or participant/attestation authorization bypass that causes invalid bridge execution or double-spend conditions."*

### Likelihood Explanation

The scenario is realistic in several production situations:

- **Contract upgrades / migrations**: The same MPC key material is intentionally shared between the old and new contract during a migration window.
- **Parallel bridge deployments**: Multiple bridge contracts (e.g., per-asset or per-chain bridges) backed by the same MPC network and the same `ForeignTx` domain key.
- **Testnet/mainnet key confusion**: If the same DKG output is accidentally used across environments.

The attacker requires no privileged access: the response is publicly visible on-chain, and `respond_verify_foreign_tx` is callable by any attested MPC participant (or, in a degraded scenario, by anyone who can submit the transaction).

### Recommendation

Include the NEAR contract account ID and the `domain_id` in the signed payload, analogous to EIP-712's domain separator. For example, extend `ForeignTxSignPayloadV1` to:

```rust
pub struct ForeignTxSignPayloadV1 {
    pub contract_id: AccountId,   // env::current_account_id() at request time
    pub domain_id: DomainId,
    pub request: ForeignChainRpcRequest,
    pub values: Vec<ExtractedValue>,
}
```

The contract should populate `contract_id` and `domain_id` when constructing the payload before hashing, and `respond_verify_foreign_tx` should recompute the expected hash from the stored request (rather than trusting `response.payload_hash` directly) to verify the binding.

### Proof of Concept

1. Deploy two MPC contract instances **A** (`mpc-a.near`) and **B** (`mpc-b.near`) backed by the same MPC key set (same DKG output, same `domain_id = 0`).
2. User submits `verify_foreign_transaction` for Bitcoin tx `X` on contract **A**.
3. MPC nodes process the request, query the Bitcoin RPC, and produce a valid `VerifyForeignTransactionResponse { payload_hash: H, signature: σ }` where `H = SHA-256(borsh(ForeignTxSignPayloadV1 { request: X, values: [block_hash_Y] }))`.
4. The response is submitted to contract **A** via `respond_verify_foreign_tx`; the bridge contract on **A** credits the inbound transfer.
5. Attacker (or any observer) reads `(H, σ)` from the NEAR transaction log.
6. A second user submits `verify_foreign_transaction` for the same Bitcoin tx `X` on contract **B** (same `domain_id`).
7. Attacker calls `respond_verify_foreign_tx` on contract **B** with the replayed `{ payload_hash: H, signature: σ }`.
8. Contract **B** computes `self.public_key_extended(domain_id)` → same root key → `verify_ecdsa_signature(σ, H, root_pk)` succeeds.
9. Contract **B** resolves the pending yield and returns the response to the bridge contract on **B**, which credits the same inbound transfer a second time — a double-spend.

The root cause is confirmed at: [1](#0-0) [5](#0-4) [6](#0-5)

### Citations

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L1499-1502)
```rust
pub struct ForeignTxSignPayloadV1 {
    pub request: ForeignChainRpcRequest,
    pub values: Vec<ExtractedValue>,
}
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
