### Title
Forged Foreign-Chain Verification via Response Replay in `respond_verify_foreign_tx` — (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_verify_foreign_tx` verifies that the submitted ECDSA signature is valid over the caller-supplied `response.payload_hash`, but never checks that `payload_hash` actually corresponds to the `request` being resolved. A single Byzantine attested participant (below signing threshold) can replay any previously observed valid `VerifyForeignTransactionResponse` as the answer to a completely different pending request, causing bridge contracts to receive a cryptographically valid but semantically forged foreign-chain attestation.

---

### Finding Description

In `respond_verify_foreign_tx`, the contract performs two independent checks and then drains the pending yield queue:

```rust
// 1. Verify signature over the caller-supplied payload_hash
let payload_hash: [u8; 32] = response.payload_hash.0;
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,
).is_ok()

// 2. Drain the pending queue for `request`
pending_requests::resolve_yields_for(
    &mut self.pending_verify_foreign_tx_requests,
    &request,
    serde_json::to_vec(&response).unwrap(),
)
``` [1](#0-0) 

The signed payload that MPC nodes produce is `SHA-256(borsh(ForeignTxSignPayloadV1 { request: ForeignChainRpcRequest, values: Vec<ExtractedValue> }))`. [2](#0-1) 

The contract receives only the hash (`response.payload_hash`) and the signature — never the `values`. It therefore has no way to reconstruct the expected hash from the `request` argument and compare. The two checks are entirely decoupled: the signature can be valid over a hash that has nothing to do with the `request` whose yield queue is being drained.

**Attack path (single Byzantine attested participant, below threshold):**

1. Participant P observes a legitimately completed `respond_verify_foreign_tx` call on-chain for `request_A` (e.g., Bitcoin `tx_id=X`, `block_hash=0xabc`). The full `VerifyForeignTransactionResponse` — `payload_hash_A` and `signature_A` — is public on-chain.
2. Victim submits `verify_foreign_transaction(request_B)` for a different transaction (`tx_id=Y`). A yield is queued under `request_B`.
3. P calls `respond_verify_foreign_tx(request_B, response_A)`. The contract checks:
   - Caller is an attested participant ✓
   - `signature_A` is valid over `payload_hash_A` ✓ (the key hasn't changed)
   - `pending_verify_foreign_tx_requests` has an entry for `request_B` ✓
4. The contract drains the yield for `request_B` with `response_A`. The victim's transaction receives `payload_hash_A` (bound to `tx_id=X`) and `signature_A` as the verified result for `tx_id=Y`.

The `domain_id` and `payload_version` fields of `VerifyForeignTransactionRequest` are also absent from the signed payload, but cross-domain replay is blocked by different per-domain keys. The core gap is the missing binding between `payload_hash` and the `ForeignChainRpcRequest` being resolved. [3](#0-2) 

---

### Impact Explanation

A bridge contract (e.g., Omnibridge inbound flow) submits `verify_foreign_transaction(tx_id=Y)` to confirm a deposit. It receives a `VerifyForeignTransactionResponse` with a valid MPC signature. The bridge cannot independently verify that `payload_hash` corresponds to `tx_id=Y` because it does not know the extracted values in advance — that is precisely what it is asking the MPC to determine. The SDK's `ForeignChainSignatureVerifier::verify_signature` requires the caller to supply `expected_extracted_values`, which the bridge does not have. [4](#0-3) 

The bridge therefore accepts the forged response and acts on extracted values (block hash, log data, etc.) that belong to a completely different transaction. This enables invalid bridge execution — for example, crediting a deposit that was never made on the foreign chain — matching the "High" impact category of cross-chain replay / forged foreign-chain verification causing invalid bridge execution.

---

### Likelihood Explanation

- The attacker must be a single attested MPC participant (below signing threshold), which is explicitly in scope.
- All prior `respond_verify_foreign_tx` call arguments are permanently visible on-chain; no secret material is needed.
- The attacker only needs to race their replay call before a legitimate MPC response arrives for the victim's request. Because the attacker controls when they submit, they can front-run the honest nodes.
- No threshold collusion, key compromise, or network-level attack is required.

---

### Recommendation

Bind `payload_hash` to the `request` being resolved inside the contract. Two options:

1. **Include `values` in the response**: Have nodes submit the full `ForeignTxSignPayloadV1` (not just its hash). The contract reconstructs `SHA-256(borsh(payload))` and asserts it equals `response.payload_hash` before accepting. This is the most direct fix.

2. **Include the full `VerifyForeignTransactionRequest` (with `domain_id` and `payload_version`) in the signed payload**: Nodes sign `SHA-256(borsh(domain_id || payload_version || ForeignTxSignPayloadV1))`. The contract can then verify the hash includes the correct `domain_id` and `payload_version` from the `request` argument, partially closing the gap (though `values` still cannot be checked without option 1).

The analogous fix in the referenced Gateway report was to hash the full message struct and mark it as used once consumed — the equivalent here is to make `payload_hash` a deterministic function of the `request` that the contract can independently verify.

---

### Proof of Concept

```
// Setup: domain 0 (ForeignTx, Secp256k1), two Bitcoin requests

// Step 1 — legitimate flow for request_A (tx_id = [0xAA; 32])
user_A.verify_foreign_transaction({ tx_id: [0xAA;32], confirmations: 1, extractors: [BlockHash], domain_id: 0 })
// MPC nodes respond; response_A = { payload_hash_A, signature_A } is emitted on-chain

// Step 2 — victim submits request_B (tx_id = [0xBB; 32])
user_B.verify_foreign_transaction({ tx_id: [0xBB;32], confirmations: 1, extractors: [BlockHash], domain_id: 0 })
// yield queued under request_B

// Step 3 — Byzantine participant P replays response_A for request_B
P.respond_verify_foreign_tx(
    request  = { tx_id: [0xBB;32], confirmations: 1, extractors: [BlockHash], domain_id: 0 },
    response = response_A   // payload_hash and signature from tx_id=[0xAA;32]
)
// Contract checks: signature_A valid over payload_hash_A ✓
// Contract drains yield for request_B with response_A ✓
// user_B receives payload_hash_A (bound to [0xAA;32]) as the verified result for [0xBB;32]
```

The contract accepts the call at `resolve_yields_for` because the only guard is signature validity over the caller-supplied hash, not hash-to-request binding. [5](#0-4)

### Citations

**File:** crates/contract/src/lib.rs (L692-754)
```rust
    pub fn respond_verify_foreign_tx(
        &mut self,
        request: VerifyForeignTransactionRequest,
        response: VerifyForeignTransactionResponse,
    ) -> Result<(), Error> {
        let signer = Self::assert_caller_is_signer();

        log!(
            "respond_verify_foreign_tx: signer={}, request={:?}",
            &signer,
            &request
        );

        self.assert_caller_is_attested_participant_and_protocol_active();

        if !self.protocol_state.is_running_or_resharing() {
            return Err(InvalidState::ProtocolStateNotRunning.into());
        }

        if !self.accept_requests {
            return Err(TeeError::TeeValidationFailed.into());
        }

        let domain = request.domain_id;
        let public_key = self.public_key_extended(domain.0.into())?;

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
