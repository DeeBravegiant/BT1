### Title
Missing `payload_hash`-to-`request` Binding in `respond_verify_foreign_tx` Allows a Single Byzantine Participant to Deliver a Forged Foreign-Chain Verification Response - (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_verify_foreign_tx` verifies that `response.signature` is cryptographically valid over `response.payload_hash`, but never checks that `response.payload_hash` is the hash of a payload that encodes the submitted `request`. A single attested MPC participant (Byzantine, below the signing threshold) can supply a valid signature from one foreign-chain verification request as the response to a completely different pending request, causing the contract to deliver a forged verification attestation to the waiting caller.

---

### Finding Description

`respond_verify_foreign_tx` in `crates/contract/src/lib.rs` (lines 691–754) performs the following checks:

1. Caller is an attested participant.
2. Protocol is running or resharing.
3. `accept_requests` is true.
4. `response.signature` is a valid ECDSA signature over `response.payload_hash` under the domain's **root** public key. [1](#0-0) 

What it does **not** check is that `response.payload_hash` is the canonical hash of `ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 { request: <the submitted request>, values: <observed values> })`. The `payload_hash` field is entirely caller-supplied and is only verified for signature validity, not for binding to the `request` argument used to look up and drain the pending yield queue. [2](#0-1) 

By contrast, the regular `respond` function derives the payload hash directly from the stored `request.payload`, so the contract always knows exactly what was signed: [3](#0-2) 

The `ForeignTxSignPayloadV1` struct that defines what should be signed encodes both the `request` and the off-chain-observed `values`: [4](#0-3) 

Because `values` are determined off-chain, the contract cannot independently reconstruct the hash — but it also performs no partial check (e.g., verifying that the `request` portion of the payload matches). The `near-mpc-sdk`'s `ForeignChainSignatureVerifier::verify_signature` does perform this check client-side: [5](#0-4) 

However, this is an off-chain SDK helper, not enforced by the on-chain contract. The contract itself accepts any `payload_hash` that carries a valid root-key signature.

---

### Impact Explanation

A single Byzantine attested participant (strictly below the signing threshold) can:

1. Observe that the MPC network has produced a valid signature `sig_A` over `payload_hash_A` for pending request A (e.g., "Bitcoin tx `0xAA…` confirmed in block `0xBB…`").
2. Call `respond_verify_foreign_tx(request = request_B, response = { payload_hash: payload_hash_A, signature: sig_A })` where `request_B` is a different pending request (e.g., "Bitcoin tx `0xCC…`").
3. The contract accepts the call: `sig_A` is a valid root-key signature over `payload_hash_A`, and `request_B` exists in `pending_verify_foreign_tx_requests`.
4. All yields queued under `request_B` are drained and resolved with `{ payload_hash: payload_hash_A, signature: sig_A }`.
5. Every caller waiting on `request_B` receives a valid MPC attestation that actually attests to transaction A's data, not transaction B's.

Any bridge contract that trusts the returned `payload_hash` without re-computing the expected hash from its own request parameters (i.e., without using `ForeignChainSignatureVerifier`) will accept this forged attestation as proof that transaction B was confirmed, enabling invalid bridge execution or double-spend conditions.

**Impact class:** High — forged foreign-chain verification causing invalid bridge execution.

---

### Likelihood Explanation

The attacker must be a single attested MPC participant. This is a realistic threat model: the system is explicitly designed to tolerate Byzantine participants strictly below the signing threshold. The attacker does not need to forge any cryptographic material — they only need to reuse a legitimately produced signature from one request as the response to another. The attack requires no collusion, no key leakage, and no network-level access beyond what any participant already has.

---

### Recommendation

Include the extracted `values` in `VerifyForeignTransactionResponse` and have the contract verify the hash binding on-chain:

```rust
// In respond_verify_foreign_tx, after retrieving the public key:
let expected_payload = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
    request: request.request.clone(),
    values: response.values.clone(), // add values to the response DTO
});
let expected_hash = expected_payload.compute_msg_hash()
    .map_err(|_| RespondError::PayloadHashComputationFailed)?;
if expected_hash != response.payload_hash {
    return Err(RespondError::PayloadHashMismatch.into());
}
```

This mirrors the check already present in `ForeignChainSignatureVerifier::verify_signature` in the SDK, and ensures the contract itself enforces the binding between the submitted `request` and the signed `payload_hash`. [6](#0-5) 

---

### Proof of Concept

**Setup:** Two pending `verify_foreign_transaction` requests exist:
- `request_A`: Bitcoin tx `[0xAA; 32]`, 1 confirmation
- `request_B`: Bitcoin tx `[0xBB; 32]`, 6 confirmations

**Step 1:** MPC network legitimately processes `request_A` and produces:
- `payload_hash_A = SHA-256(borsh(ForeignTxSignPayload::V1 { request: request_A, values: [BlockHash([0x11;32])] }))`
- `sig_A` = valid ECDSA signature over `payload_hash_A` under the domain root key

**Step 2:** Byzantine participant calls:
```
respond_verify_foreign_tx(
    request = request_B,
    response = VerifyForeignTransactionResponse {
        payload_hash: payload_hash_A,  // hash of tx A's payload
        signature: sig_A,              // valid signature over payload_hash_A
    }
)
```

**Step 3:** Contract checks (lines 718–747):
- `sig_A` valid over `payload_hash_A` under root key? ✅ Yes.
- `request_B` in `pending_verify_foreign_tx_requests`? ✅ Yes.

**Step 4:** `resolve_yields_for` drains `request_B`'s yield queue with `{ payload_hash_A, sig_A }`. [7](#0-6) 

**Result:** The caller waiting on `request_B` receives a valid MPC attestation for Bitcoin tx `[0xAA; 32]` (transaction A), not `[0xBB; 32]` (transaction B). A bridge contract that does not re-derive the expected hash will accept this as proof that transaction B was confirmed, enabling fraudulent bridge execution.

### Citations

**File:** crates/contract/src/lib.rs (L600-608)
```rust
                let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");

                // Check the signature is correct
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    payload_hash,
                    &expected_public_key,
                )
                .is_ok()
```

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

**File:** crates/near-mpc-sdk/src/foreign_chain.rs (L47-64)
```rust
    ) -> Result<(), VerifyForeignChainError> {
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
