### Title
`respond_verify_foreign_tx` Does Not Bind `payload_hash` to `request` — Cross-Request Signature Replay Enables Forged Foreign-Chain Verification - (File: `crates/contract/src/lib.rs`)

---

### Summary

The `respond_verify_foreign_tx` contract method verifies only that the submitted `signature` is a valid ECDSA signature over the caller-supplied `payload_hash` using the root MPC public key. It does **not** verify that `payload_hash` was actually derived from the accompanying `request` (i.e., `SHA-256(borsh(ForeignTxSignPayload::V1 { request, values }))`). A single attested MPC participant can therefore replay any previously obtained root-key signature — produced by threshold cooperation for a different `verify_foreign_transaction` request — to immediately resolve any pending request with a fabricated `payload_hash`, bypassing the foreign-chain verification guarantee entirely.

---

### Finding Description

`respond_verify_foreign_tx` in `crates/contract/src/lib.rs` performs two checks before resolving pending yields:

1. The caller is an attested participant.
2. `response.signature` is a valid ECDSA signature over `response.payload_hash` under the domain's root public key.

```rust
// crates/contract/src/lib.rs  lines 718–734
let payload_hash: [u8; 32] = response.payload_hash.0;
// Check the signature is correct against the root public key
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,
)
.is_ok()
```

After passing those checks the contract resolves all queued yields for `request` with the full `response` struct:

```rust
// crates/contract/src/lib.rs  lines 749–753
pending_requests::resolve_yields_for(
    &mut self.pending_verify_foreign_tx_requests,
    &request,
    serde_json::to_vec(&response).unwrap(),
)
```

The canonical payload that the MPC nodes are supposed to sign is:

```rust
// crates/near-mpc-contract-interface/src/types/foreign_chain.rs  lines 1499–1509
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
}
```

The contract never reconstructs `ForeignTxSignPayload` from `request` and checks `payload_hash == compute_msg_hash()`. The binding between `payload_hash` and `request` is entirely off-chain and unenforced on-chain.

**Attack path:**

1. The MPC network (threshold cooperation) processes a legitimate `verify_foreign_transaction` for `request_A`, producing `(payload_hash_A, sig_A)` where `payload_hash_A = SHA-256(borsh(ForeignTxSignPayload::V1 { request: rpc_A, values: values_A }))`. This response is published on-chain and is publicly observable.
2. A single malicious attested MPC node submits a new `verify_foreign_transaction` for `request_B` (a different foreign-chain transaction, e.g., a non-existent or already-spent one).
3. Before the honest nodes can respond, the malicious node calls `respond_verify_foreign_tx(request=request_B, response={ payload_hash: payload_hash_A, sig: sig_A })`.
4. The contract accepts: `sig_A` is a valid root-key signature over `payload_hash_A`. It resolves all yields for `request_B` with `{ payload_hash: payload_hash_A, sig: sig_A }`.
5. The caller of `request_B` receives a response whose `payload_hash` encodes the foreign-chain data for `request_A`, not `request_B`. Because the caller does not know the extracted `values`, they cannot independently recompute the expected hash and detect the forgery.

---

### Impact Explanation

The `verify_foreign_transaction` flow is the MPC network's mechanism for attesting that a specific foreign-chain transaction occurred and extracting typed values from it (block hashes, log data, etc.). Bridge contracts on NEAR are expected to consume the returned `(payload_hash, signature)` to gate fund releases or state transitions.

A single malicious attested participant (strictly below the signing threshold) can forge a `verify_foreign_transaction` response for any pending request using a previously obtained root-key signature. The forged response carries a `payload_hash` that encodes the data of a *different* foreign-chain transaction. Because callers cannot independently verify the `payload_hash` without knowing the extracted `values`, they cannot detect the forgery. This enables:

- **Invalid bridge execution**: a bridge contract releases funds on NEAR for a foreign-chain transaction that was never verified (or was already spent).
- **Double-spend**: the same foreign-chain transaction's signature can be replayed to resolve multiple independent `request_B` submissions.

This matches the allowed impact: *"High. Cross-chain replay, forged foreign-chain verification … that causes invalid bridge execution or double-spend conditions."*

---

### Likelihood Explanation

- The attacker must be an attested MPC participant — a legitimate node in the network. This is a realistic Byzantine-participant assumption explicitly in scope.
- The attacker needs one prior root-key signature (from any previously processed `verify_foreign_transaction`). Such signatures are published on-chain and are publicly observable from the first successful foreign-chain verification onward.
- The attacker must submit the forged response before honest nodes respond. Honest nodes must query a foreign-chain RPC and run threshold signing (seconds to tens of seconds). The malicious node skips both steps and can submit immediately after observing the new request on-chain.
- No special tooling is required beyond a standard NEAR function call.

---

### Recommendation

The contract must enforce that `payload_hash` is the canonical hash of the `request` field. Since the contract does not know the extracted `values`, the simplest fix is to require the MPC nodes to include the full `ForeignTxSignPayload` (not just its hash) in the response, and have the contract recompute and verify the hash:

```rust
// In respond_verify_foreign_tx, after verifying the signature:
let expected_hash = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
    request: request.request.clone(),
    values: response.values.clone(), // values must be added to the response DTO
}).compute_msg_hash()?;
if expected_hash != response.payload_hash {
    return Err(RespondError::PayloadHashMismatch.into());
}
```

Alternatively, include a domain-separation tag or the `request` identifier in the signed payload at the protocol level so that a signature produced for `request_A` is cryptographically invalid for `request_B`.

---

### Proof of Concept

```
// Setup: MPC network has processed request_A for Bitcoin tx 0xAA...AA
// payload_hash_A = SHA256(borsh(ForeignTxSignPayload::V1 { request: Bitcoin(0xAA), values: [BlockHash(0xBB)] }))
// sig_A = root_key.sign(payload_hash_A)   ← published on-chain, publicly visible

// Attacker (single attested node):
// 1. Submit a new verify_foreign_transaction for a fabricated Bitcoin tx 0xCC...CC
contract.verify_foreign_transaction({
    domain_id: foreign_tx_domain,
    payload_version: V1,
    request: Bitcoin { tx_id: 0xCC..CC, confirmations: 1, extractors: [BlockHash] }
});

// 2. Immediately call respond_verify_foreign_tx with the replayed signature
contract.respond_verify_foreign_tx(
    request = VerifyForeignTransactionRequest {
        domain_id: foreign_tx_domain,
        payload_version: V1,
        request: Bitcoin { tx_id: 0xCC..CC, ... }   // matches the pending request
    },
    response = VerifyForeignTransactionResponse {
        payload_hash: payload_hash_A,   // hash of request_A's data, NOT request_B's
        signature: sig_A,               // valid root-key signature over payload_hash_A
    }
);

// Contract accepts: sig_A verifies over payload_hash_A under root key ✓
// Yields for 0xCC..CC are resolved with payload_hash_A and sig_A
// Caller receives a "verified" response for a transaction that was never inspected
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/contract/src/lib.rs (L691-697)
```rust
    #[handle_result]
    pub fn respond_verify_foreign_tx(
        &mut self,
        request: VerifyForeignTransactionRequest,
        response: VerifyForeignTransactionResponse,
    ) -> Result<(), Error> {
        let signer = Self::assert_caller_is_signer();
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

**File:** crates/contract/src/lib.rs (L749-753)
```rust
        pending_requests::resolve_yields_for(
            &mut self.pending_verify_foreign_tx_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
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
