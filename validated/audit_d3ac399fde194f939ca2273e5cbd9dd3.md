### Title
Missing `payload_hash`-to-request binding in `respond_verify_foreign_tx` enables cross-request signature replay - (File: crates/contract/src/lib.rs)

### Summary
The `respond_verify_foreign_tx` function verifies that the submitted signature is valid for the submitted `payload_hash`, but never verifies that `payload_hash` was actually derived from the `request` parameter. A single malicious attested participant can replay a previously obtained `(payload_hash, signature)` pair from any prior signing round as the response to a different pending request, delivering a forged foreign-chain verification result to the user.

### Finding Description
In `respond_verify_foreign_tx` (lines 691–754 of `crates/contract/src/lib.rs`), the contract performs the following checks:

1. Caller is an attested participant (`assert_caller_is_attested_participant_and_protocol_active`).
2. The signature in `response.signature` is valid for `response.payload_hash` against the domain's root public key.
3. A pending request matching `request` exists in `pending_verify_foreign_tx_requests`.

What it does **not** check is that `response.payload_hash` was actually derived from `request.request`. The canonical `payload_hash` is defined as:

```
payload_hash = SHA-256(borsh(ForeignTxSignPayload::V1 { request: request.request, values: extracted_values }))
```

as documented in `crates/near-mpc-contract-interface/src/types/foreign_chain.rs` (lines 1504–1509) and `docs/foreign-chain-transactions.md` (lines 182–186). The contract never recomputes or validates this binding. The `payload_hash` field in `VerifyForeignTransactionResponse` is entirely attacker-controlled, subject only to the constraint that the signature must be valid for it.

The `near-mpc-sdk`'s `ForeignChainSignatureVerifier::verify_signature` (lines 42–89 of `crates/near-mpc-sdk/src/foreign_chain.rs`) does perform this binding check on the client side, but the on-chain contract does not enforce it. Users who do not use the SDK, or who use it incorrectly, receive no protection from the contract itself.

The contrast with the `respond` function for ordinary signatures is instructive: there, the contract recomputes the expected derived public key from `request.tweak` and verifies the signature against it, binding the response to the request. No equivalent binding exists for `respond_verify_foreign_tx`.

### Impact Explanation
A single malicious attested participant (strictly below the signing threshold) that has previously participated in any legitimate signing round for any foreign-chain request can save the resulting `(payload_hash, signature)` pair. It can then call `respond_verify_foreign_tx` with a different pending `request` as the key but the saved `(payload_hash, signature)` as the response body. The contract will:

- Confirm the signature is valid for `payload_hash` → passes.
- Find the pending request by key → passes.
- Deliver the forged `VerifyForeignTransactionResponse` to the user.

The user's smart contract receives a `payload_hash` that corresponds to a completely different transaction (different `tx_id`, different chain, or different extracted values). Any bridge logic that trusts the contract-delivered response without independently re-verifying the hash binding will act on fabricated foreign-chain state, enabling invalid bridge execution or double-spend conditions.

This matches the allowed High impact: **"Cross-chain replay, forged foreign-chain verification, light-client-style verification bypass, or participant/attestation authorization bypass that causes invalid bridge execution or double-spend conditions."**

### Likelihood Explanation
The attack requires only a single malicious attested participant — strictly below the signing threshold — who has participated in at least one prior legitimate signing round and retained the resulting `(payload_hash, signature)` pair. No key forgery, no threshold collusion, and no TEE break is required. In a production network processing many foreign-chain verification requests, any attested node accumulates a library of valid `(payload_hash, signature)` pairs it can replay against any future pending request. The entry path is the publicly callable `respond_verify_foreign_tx` method, reachable by any attested participant.

### Recommendation
The contract should enforce the binding between `response.payload_hash` and `request.request` on-chain. The most direct fix is to include the `values` (extracted values) in the `VerifyForeignTransactionResponse`, allowing the contract to recompute:

```rust
let expected_hash = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
    request: request.request.clone(),
    values: response.values.clone(),
}).compute_msg_hash()?;
if expected_hash != response.payload_hash {
    return Err(RespondError::PayloadHashMismatch.into());
}
```

This mirrors the binding already enforced by `ForeignChainSignatureVerifier::verify_signature` in the SDK and closes the gap between off-chain and on-chain validation.

### Proof of Concept

1. User A submits `verify_foreign_transaction` for `Bitcoin(tx_id=0x1111, extractors=[BlockHash])`. The MPC network signs `payload_hash_A = SHA-256(borsh({ request: Bitcoin(tx_id=0x1111,...), values: [block_hash=0xaaaa] }))`. Malicious node M saves `(payload_hash_A, sig_A)`.

2. User B submits `verify_foreign_transaction` for `Bitcoin(tx_id=0x2222, extractors=[BlockHash])`. A pending entry is created under key `VerifyForeignTransactionRequest { request: Bitcoin(tx_id=0x2222,...), ... }`.

3. Malicious node M calls:
   ```
   respond_verify_foreign_tx(
       request = VerifyForeignTransactionRequest { request: Bitcoin(tx_id=0x2222,...), ... },
       response = VerifyForeignTransactionResponse { payload_hash: payload_hash_A, signature: sig_A }
   )
   ```

4. The contract at lines 718–734 verifies `sig_A` against `payload_hash_A` and the root public key → **passes** (signature is genuinely from the MPC network).

5. The contract at lines 749–753 resolves the pending request for `tx_id=0x2222` and delivers `{ payload_hash: payload_hash_A, signature: sig_A }` to User B.

6. User B's smart contract receives a response whose `payload_hash` encodes `tx_id=0x1111` and `block_hash=0xaaaa` — fabricated state for a transaction User B never submitted. Any bridge logic acting on this response operates on forged foreign-chain data.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L147-150)
```rust
pub struct VerifyForeignTransactionResponse {
    pub payload_hash: Hash256,
    pub signature: SignatureResponse,
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

**File:** crates/near-mpc-sdk/src/foreign_chain.rs (L48-64)
```rust
        let expected_payload = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
            request: self.request,
            values: self.expected_extracted_values,
        });

        let expected_payload_hash = expected_payload
            .compute_msg_hash()
            .map_err(|_| VerifyForeignChainError::FailedToComputeMsgHash)?;

        let payload_is_correct = expected_payload_hash == response.payload_hash;

        if !payload_is_correct {
            return Err(VerifyForeignChainError::IncorrectPayloadSigned {
                got: response.payload_hash.clone(),
                expected: expected_payload_hash,
            });
        }
```
