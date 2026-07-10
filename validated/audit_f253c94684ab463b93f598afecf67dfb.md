### Title
`ForeignTxSignPayload::compute_msg_hash()` Lacks Request-Context Binding, Enabling Cross-Request Signature Replay in `respond_verify_foreign_tx` — (File: `crates/near-mpc-contract-interface/src/types/foreign_chain.rs`)

---

### Summary

`ForeignTxSignPayload::compute_msg_hash()` produces a hash that covers only the foreign-chain request data and extracted values. It omits the NEAR contract account ID, NEAR chain ID, `domain_id`, and `payload_version`. More critically, `respond_verify_foreign_tx` in the MPC contract verifies that the submitted signature is valid over `response.payload_hash`, but **never checks that `response.payload_hash` actually corresponds to the `request` being resolved**. A single Byzantine attested MPC participant can replay any previously observed valid `VerifyForeignTransactionResponse` to resolve a completely different pending request, delivering a forged verification result to the waiting caller.

---

### Finding Description

`ForeignTxSignPayload::compute_msg_hash()` computes:

```
msg_hash = SHA-256(borsh(ForeignTxSignPayload { request: ForeignChainRpcRequest, values: Vec<ExtractedValue> }))
``` [1](#0-0) 

The struct contains only the foreign-chain RPC request and extracted values — no NEAR contract address, no NEAR chain ID, no `domain_id`, no `payload_version`. [2](#0-1) 

In `respond_verify_foreign_tx`, the contract verifies the signature over `response.payload_hash` using the domain's public key: [3](#0-2) 

After the signature check passes, it immediately resolves all pending yields for `request` with the full `response`: [4](#0-3) 

**There is no check that `response.payload_hash` equals `ForeignTxSignPayload { request: request.request, values: <observed_values> }.compute_msg_hash()`**. The contract only confirms the signature is cryptographically valid over *some* hash — not that the hash is the one derived from the specific `request` being resolved.

---

### Impact Explanation

A single Byzantine attested MPC participant (below threshold) can:

1. Observe any previously completed `VerifyForeignTransactionResponse` on-chain for request R1 (tx_id=X): `{payload_hash=H1, signature=S1}`. This is public state.
2. Wait for a different pending request R2 (tx_id=Y) to appear in `pending_verify_foreign_tx_requests`.
3. Call `respond_verify_foreign_tx(request=R2, response={payload_hash=H1, signature=S1})`.
4. The contract verifies S1 is valid over H1 — it is, because S1 was legitimately produced by the MPC network for H1. The check passes.
5. `resolve_yields_for` drains R2's yield queue with `{payload_hash=H1, signature=S1}`. [5](#0-4) 

The caller of R2 receives a `VerifyForeignTransactionResponse` whose `payload_hash` corresponds to tx X, not tx Y. Any bridge contract that does not independently re-verify the payload hash via `ForeignChainSignatureVerifier::verify_signature` will accept this as proof that tx Y was verified on the foreign chain, enabling forged foreign-chain verification and potential double-spend or invalid bridge execution. [6](#0-5) 

The SDK's `verify_signature` does catch the mismatch, but the contract itself provides no on-chain enforcement — callers that skip SDK verification are fully exposed.

---

### Likelihood Explanation

**Medium-High.** The attacker needs only to be a single attested MPC participant — no threshold collusion required. All prior `VerifyForeignTransactionResponse` values are permanently visible on-chain. The attacker can monitor the contract for new pending requests and immediately replay any previously valid response. The only constraint is that the target request must be pending at the time of the call.

---

### Recommendation

In `respond_verify_foreign_tx`, recompute the expected payload hash from `request` and the extracted values embedded in `response.payload_hash`, and assert equality before resolving yields. Concretely, the contract should reconstruct `ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 { request: request.request.clone(), values: ... })` and verify that `compute_msg_hash()` matches `response.payload_hash`. Since the contract does not independently fetch extracted values, the simplest fix is to include the `request` key (including `domain_id` and `payload_version`) in the signed payload — i.e., add these fields to `ForeignTxSignPayloadV1` — so that a signature produced for one request is cryptographically invalid for any other. This is the direct analog of the EIP-712 `domainSeparator` fix applied in the referenced report. [7](#0-6) 

---

### Proof of Concept

**Setup:** MPC contract deployed at `v1.signer.near` with a `ForeignTx` domain (domain_id=1, Secp256k1 key K).

**Step 1 — Obtain a legitimate response for R1:**
```
// R1: verify Bitcoin tx_id = [0xAA; 32]
verify_foreign_transaction({ domain_id: 1, request: Bitcoin { tx_id: [0xAA;32], ... } })
// MPC nodes sign: H1 = SHA-256(borsh(ForeignTxSignPayload::V1 { request: Bitcoin{tx_id:[0xAA;32],...}, values: [BlockHash([0xBB;32])] }))
// respond_verify_foreign_tx(R1, { payload_hash: H1, signature: S1 })  ← recorded on-chain
```

**Step 2 — Target a different pending request R2:**
```
// R2: verify Bitcoin tx_id = [0xCC; 32]  (different transaction)
verify_foreign_transaction({ domain_id: 1, request: Bitcoin { tx_id: [0xCC;32], ... } })
// R2 is now pending in pending_verify_foreign_tx_requests
```

**Step 3 — Byzantine participant replays S1 for R2:**
```rust
// Attacker (single attested participant) calls:
respond_verify_foreign_tx(
    request = VerifyForeignTransactionRequest { domain_id: 1, request: Bitcoin { tx_id: [0xCC;32], ... } },
    response = VerifyForeignTransactionResponse { payload_hash: H1, signature: S1 }
    //                                            ^^^ hash of tx_id=[0xAA;32], not [0xCC;32]
)
```

**Step 4 — Contract accepts:**
```
// verify_ecdsa_signature(S1, H1, K) → Ok  ← passes, S1 is valid over H1
// resolve_yields_for(R2, {payload_hash: H1, signature: S1}) → drains R2's yield
```

**Step 5 — Caller of R2 receives forged result:**
```
// Caller receives VerifyForeignTransactionResponse { payload_hash: H1, signature: S1 }
// H1 proves tx_id=[0xAA;32] was confirmed — NOT tx_id=[0xCC;32]
// Bridge contract that trusts this response executes based on wrong tx
```

The root cause is confirmed at: [8](#0-7) 
— `payload_hash` from the response is verified against the domain key, but never bound to the `request` argument passed in the same call.

### Citations

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

**File:** crates/contract/src/lib.rs (L718-743)
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
```

**File:** crates/contract/src/lib.rs (L749-753)
```rust
        pending_requests::resolve_yields_for(
            &mut self.pending_verify_foreign_tx_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
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

**File:** crates/near-mpc-sdk/src/foreign_chain.rs (L41-89)
```rust
impl ForeignChainSignatureVerifier {
    pub fn verify_signature(
        self,
        response: &VerifyForeignTransactionResponse,
        // TODO(#2232): don't use interface API types for public keys
        public_key: &PublicKey,
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
        let verification_result = match (public_key, &response.signature) {
            (
                PublicKey::Secp256k1(secp256k1_public_key),
                SignatureResponse::Secp256k1(k256_signature),
            ) => near_mpc_signature_verifier::verify_ecdsa_signature(
                k256_signature,
                &expected_payload_hash,
                secp256k1_public_key,
            ),
            (PublicKey::Ed25519(ed25519_public_key), SignatureResponse::Ed25519 { signature }) => {
                near_mpc_signature_verifier::verify_eddsa_signature(
                    signature,
                    expected_payload_hash.as_slice(),
                    ed25519_public_key,
                )
            }
            // TODO(#2234): improve types so these errors can't happen
            (PublicKey::Bls12381(_bls12381_g2_public_key), _) => {
                return Err(VerifyForeignChainError::UnexpectedSignatureScheme);
            }
            _ => return Err(VerifyForeignChainError::UnexpectedSignatureScheme),
        };

        verification_result.map_err(|_| VerifyForeignChainError::SignatureVerificationFailed)
    }
```
