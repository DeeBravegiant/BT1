### Title
`respond_verify_foreign_tx()` Does Not Verify That `payload_hash` Was Derived From the Submitted `request` — (File: `crates/contract/src/lib.rs`)

### Summary

`respond_verify_foreign_tx()` verifies that the submitted signature is cryptographically valid over `response.payload_hash`, but it never checks that `payload_hash` was actually derived from the `request` that is being resolved. A single Byzantine attested participant (strictly below the signing threshold) can replay a legitimately-produced MPC signature for one foreign-chain request to satisfy a completely different pending request, delivering a forged verification response to the original caller.

### Finding Description

`respond_verify_foreign_tx()` performs two logical steps:

1. Verify `ECDSA_verify(response.signature, response.payload_hash, domain_public_key)` — i.e., the MPC network signed *something*.
2. Resolve the pending yield keyed on `request` with the raw `response` bytes. [1](#0-0) 

What is **never checked** is whether `response.payload_hash` equals `SHA-256(borsh(ForeignTxSignPayload { request, values }))` for the specific `request` being resolved. The contract only confirms that the signature is a valid MPC signature over *some* 32-byte hash; it does not confirm that hash encodes the correct request.

The `ForeignTxSignPayload` that MPC nodes sign embeds the full original request alongside the extracted values: [2](#0-1) 

Because the contract never reconstructs or compares this hash against the pending `request`, a Byzantine participant can supply `response_B` (a legitimately-signed response for request B) as the answer to request A.

### Impact Explanation

**High.** This is a cross-chain replay / forged foreign-chain verification. The primary production use-case is the Omnibridge inbound flow, where a NEAR bridge contract calls `verify_foreign_transaction` to confirm that a foreign-chain deposit occurred before releasing funds. [3](#0-2) 

A Byzantine participant replays a valid MPC signature for a different (or fabricated) transaction. The bridge contract receives a `VerifyForeignTransactionResponse` whose signature passes cryptographic verification but whose `payload_hash` encodes a different `tx_id` or different extracted values. Any bridge contract that does not independently recompute and compare `payload_hash` against its own expected `(request, values)` tuple — a check that is optional and off-chain — will accept the forged attestation and release funds for a deposit that never happened, enabling a direct double-spend.

The SDK helper `ForeignChainSignatureVerifier::verify_signature()` does perform this check: [4](#0-3) 

However, the on-chain contract enforces no such constraint, so any caller that omits or incorrectly implements this off-chain step is fully exposed.

### Likelihood Explanation

**Medium.** The attacker must be a single attested MPC participant — a realistic adversary explicitly within scope ("Byzantine participant strictly below the signing threshold"). No threshold collusion is required. The attacker does not forge a signature; they simply reuse a legitimately-produced MPC signature for one request as the response to another. The only prerequisite is that the attacker has observed at least one completed `respond_verify_foreign_tx` call for any other request on the same domain, which is publicly visible on-chain.

### Recommendation

Inside `respond_verify_foreign_tx`, after verifying the signature, recompute the minimum prefix of the expected payload hash that can be derived from the on-chain `request` alone, or — more robustly — require the caller to also supply the `extracted_values` vector and verify:

```
expected_hash = SHA-256(borsh(ForeignTxSignPayload::V1 { request, values }))
assert!(response.payload_hash == expected_hash)
```

This mirrors the check already present in the SDK verifier. Alternatively, the contract can store a commitment to the expected `payload_hash` at request submission time once the MPC nodes reach consensus on the extracted values, preventing any single participant from substituting a different hash.

### Proof of Concept

1. User Alice submits `verify_foreign_transaction(request_A)` — e.g., Bitcoin `tx_id = [0xAA; 32]`. The contract queues a pending yield for `request_A`.
2. The MPC network legitimately processes `request_B` (Bitcoin `tx_id = [0xBB; 32]`) and produces `response_B` with `payload_hash_B = SHA-256(borsh({request_B, [BlockHash([0x42;32])]}))` and a valid threshold signature `sig_B`.
3. Byzantine participant Eve (a single attested node) calls:
   ```
   respond_verify_foreign_tx(request = request_A, response = response_B)
   ```
4. The contract checks:
   - Is `request_A` in `pending_verify_foreign_tx_requests`? **Yes.**
   - Is `verify_ecdsa_signature(sig_B, payload_hash_B, domain_pk)` valid? **Yes** (it is a real MPC signature).
   - Is `payload_hash_B` derived from `request_A`? **Not checked.**
5. The contract resolves Alice's yield with `response_B`. Alice's NEAR contract receives a `VerifyForeignTransactionResponse` carrying `payload_hash_B` and `sig_B`.
6. If Alice's bridge contract only calls `verify_ecdsa_signature(sig_B, payload_hash_B, domain_pk)` — which passes — it concludes that `request_A` was verified and releases bridge funds, even though the MPC network never verified `tx_id = [0xAA; 32]`.

### Citations

**File:** crates/contract/src/lib.rs (L718-753)
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

**File:** docs/foreign-chain-transactions.md (L7-10)
```markdown
This feature lets the MPC network sign payloads only after verifying a specific foreign-chain transaction, so NEAR contracts can react to external chain events without a trusted relayer. Primary use cases:

* Omnibridge inbound flow (foreign chain -> NEAR) where Chain Signatures are required to attest that a foreign transaction finalized successfully.
* Broader chain abstraction: a single MPC network verifies foreign chain state and returns small, typed observations that contracts can interpret.
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
