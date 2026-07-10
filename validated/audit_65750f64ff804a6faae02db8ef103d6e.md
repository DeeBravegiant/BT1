### Title
Unvalidated `payload_hash` in `respond_verify_foreign_tx` Enables Cross-Request Signature Replay — (File: `crates/contract/src/lib.rs`)

### Summary

`respond_verify_foreign_tx` accepts a caller-supplied `response.payload_hash` and only verifies that the attached signature is cryptographically valid for that hash. It never checks that `payload_hash` was actually derived from the `request` that is being resolved. A single malicious attested node can replay any previously observed `(payload_hash, signature)` pair against any pending `verify_foreign_transaction` request, causing the user to receive a forged attestation over foreign-chain data they never requested.

### Finding Description

The `respond_verify_foreign_tx` function in `crates/contract/src/lib.rs` performs the following checks: [1](#0-0) 

It verifies that `response.signature` is a valid ECDSA signature over `response.payload_hash` under the domain's root public key. What it does **not** do is recompute the expected hash from the `request` argument and compare it to `response.payload_hash`.

The correct hash is defined as:

```
payload_hash = SHA-256(borsh(ForeignTxSignPayload { request, values }))
``` [2](#0-1) 

The `payload_hash` field in `VerifyForeignTransactionResponse` is entirely caller-controlled: [3](#0-2) 

Every prior successful `respond_verify_foreign_tx` call emits a `(payload_hash, signature)` pair on-chain (returned to the user via the yield/resume mechanism). Any attested participant can observe these pairs and replay them.

The analog to the DCA bug is direct:

| DCA | NEAR MPC |
|---|---|
| `takerInteraction` is caller-controlled; if empty, `curTakerFillAmount = 0` | `response.payload_hash` is caller-controlled; no check that it matches `request` |
| Contract transfers `order.inAmount` to taker for free | Contract resolves pending yield with a forged attestation |
| Fix: `require(curTakerFillAmount >= order.minOutAmountPerCycle)` | Fix: recompute expected hash from `request` and assert equality |

### Impact Explanation

A single malicious attested node (below the signing threshold) can:

1. Observe a previously resolved `verify_foreign_transaction` response for request A, obtaining `(H_A, sig_A)` where `sig_A` is a valid MPC root-key signature over `H_A`.
2. Wait for a new pending request B (e.g., a bridge deposit claim for a different transaction).
3. Call `respond_verify_foreign_tx(request=B, response={payload_hash=H_A, signature=sig_A})`.
4. The contract finds request B in the pending queue, verifies `sig_A` is valid for `H_A` under the root key (it is), and resolves request B with the forged response.
5. The user of request B receives `{payload_hash=H_A, signature=sig_A}` — a valid MPC signature attesting to the data of a completely different foreign transaction.

A bridge contract consuming this response without independently recomputing the expected hash would accept the forged attestation as proof that a foreign-chain event occurred, enabling invalid bridge execution or double-spend conditions (e.g., the same foreign deposit being claimed multiple times using the same `(H_A, sig_A)` pair against multiple pending requests).

The client-side SDK does perform this check: [4](#0-3) 

However, this is off-chain, optional, and not enforced by the contract. The contract is the trust anchor; the invariant must be enforced there.

### Likelihood Explanation

The attacker must be a single attested participant — a realistic adversary in the "Byzantine participant strictly below the signing threshold" category explicitly listed in scope. No threshold collusion is required. The `(payload_hash, signature)` pairs needed for the replay are publicly observable on-chain from any prior resolved request. The attack is deterministic and requires no special timing or race conditions beyond having a pending request to target.

### Recommendation

In `respond_verify_foreign_tx`, after verifying the signature, recompute the expected `payload_hash` from the `request` argument and assert it matches `response.payload_hash`:

```rust
// After signature verification, before resolving yields:
let expected_payload = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
    request: request.request.clone(),
    values: response.extracted_values.clone(), // or recompute from request
});
let expected_hash = expected_payload.compute_msg_hash()
    .map_err(|_| RespondError::InvalidPayloadHash)?;
if expected_hash != response.payload_hash {
    return Err(RespondError::InvalidPayloadHash.into());
}
```

Alternatively, the contract can enforce that `payload_hash` is bound to the `request` by including the `request` hash in the signed payload at the protocol level, making it impossible to produce a valid signature for one request and replay it against another.

### Proof of Concept

1. Alice submits `verify_foreign_transaction` for Bitcoin tx `[0xAA; 32]`. MPC nodes verify it and call `respond_verify_foreign_tx(request_A, {payload_hash: H_A, signature: sig_A})`. Alice receives `{H_A, sig_A}` on-chain.

2. Bob submits `verify_foreign_transaction` for Bitcoin tx `[0xBB; 32]`. Request B is now pending.

3. Malicious attested node calls:
   ```
   respond_verify_foreign_tx(
     request = request_B,   // Bob's pending request
     response = { payload_hash: H_A, signature: sig_A }  // Alice's old response
   )
   ```

4. Contract at line 729–734 verifies `sig_A` over `H_A` under root key → valid. [5](#0-4) 

5. Contract at line 749–753 resolves Bob's pending yield with `{H_A, sig_A}`. [6](#0-5) 

6. Bob receives a valid MPC signature attesting to Alice's Bitcoin transaction data, not his own. Any bridge contract that trusts this response will process Bob's request as if Alice's transaction occurred.

### Citations

**File:** crates/contract/src/lib.rs (L718-747)
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
