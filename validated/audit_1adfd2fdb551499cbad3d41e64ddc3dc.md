### Title
`respond_verify_foreign_tx` Does Not Bind `response.payload_hash` to the Submitted `request` — Forged Foreign-Chain Attestation via Cross-Request Signature Replay - (File: `crates/contract/src/lib.rs`)

### Summary

`respond_verify_foreign_tx` verifies only that the submitted signature is cryptographically valid over the caller-supplied `response.payload_hash`. It never checks that `payload_hash` is actually derived from the `request` that is being resolved. A single malicious attested participant can therefore take a legitimate MPC signature produced for one pending foreign-chain request and use it to resolve a completely different pending request, delivering a forged attestation to the original caller.

### Finding Description

The contract's `respond_verify_foreign_tx` performs three checks before resolving a pending yield:

1. Caller is an attested participant (`assert_caller_is_attested_participant_and_protocol_active`).
2. Protocol is running or resharing.
3. The ECDSA signature in `response` is valid over `response.payload_hash` under the domain's root public key. [1](#0-0) 

What is **absent** is any check that `response.payload_hash` is the SHA-256 of `borsh(ForeignTxSignPayload { request: request.request, values: <observed_values> })`. The contract has no way to reconstruct the hash from the request alone (it does not know the extracted values), so it simply trusts whatever hash the responding node supplies. [2](#0-1) 

The canonical hash structure is: [3](#0-2) 

The SDK-level verifier (`ForeignChainSignatureVerifier::verify_signature`) does enforce this binding — it recomputes the expected hash from `(request, expected_extracted_values)` and compares it to `response.payload_hash`: [4](#0-3) 

But the on-chain contract does not call this verifier; it only checks the raw ECDSA validity. The resolved yield is then delivered to the original caller with whatever `payload_hash` the malicious node supplied: [5](#0-4) 

**Attack path (single Byzantine attested participant, no threshold collusion required):**

1. Two users independently submit `verify_foreign_transaction` — Alice for Bitcoin `tx_id=X` (`request_A`) and Bob for Bitcoin `tx_id=Y` (`request_B`). Both are queued in `pending_verify_foreign_tx_requests`.
2. The MPC network honestly processes `request_B` and produces a valid threshold signature `sig_B` over `hash_B = SHA256(borsh(ForeignTxSignPayload{Bitcoin(tx_id=Y), [BlockHash(Z)]}))`. The malicious participant observes `(hash_B, sig_B)` as a co-signer.
3. Before the honest leader submits `respond_verify_foreign_tx(request_B, response_B)`, the malicious participant calls `respond_verify_foreign_tx(request_A, response_B)` — supplying Alice's request key but Bob's response.
4. The contract checks: is `sig_B` valid over `hash_B`? **Yes.** Does `request_A` exist in the pending map? **Yes.** No further check is performed.
5. Alice's yield is resumed with `response_B`. She receives a `VerifyForeignTransactionResponse` containing a valid MPC signature over a hash that encodes `tx_id=Y` and `BlockHash(Z)`, not her `tx_id=X`.
6. The honest leader then calls `respond_verify_foreign_tx(request_B, response_B)` — Bob's yield is also resolved normally (or returns `RequestNotFound` if the map entry was already drained).

### Impact Explanation

Alice's NEAR contract receives a `VerifyForeignTransactionResponse` bearing a genuine MPC threshold signature, but the signed payload attests to a **different** foreign-chain transaction (Bob's `tx_id=Y`) with **different** extracted values. Any bridge or application contract that does not independently recompute the expected `payload_hash` from its own request parameters (as the SDK verifier does) will accept this forged attestation as proof that Alice's transaction was verified. This enables invalid bridge execution — e.g., crediting a deposit that never occurred, or attesting to a block hash that belongs to a different transaction — constituting a forged foreign-chain verification and potential double-spend condition.

This matches the allowed High impact: **"Cross-chain replay, forged foreign-chain verification, light-client-style verification bypass, or participant/attestation authorization bypass that causes invalid bridge execution or double-spend conditions."**

### Likelihood Explanation

- Requires only **one** malicious attested participant — well within the Byzantine fault model the system is designed to tolerate.
- No threshold collusion, no key leakage, no physical TEE attack needed.
- The attacker observes `(hash_B, sig_B)` legitimately as a co-signer in the MPC round for `request_B`.
- The window for the attack is the time between the MPC round completing and the honest leader submitting `respond_verify_foreign_tx(request_B, ...)`. In practice this is seconds to minutes, ample time for a racing on-chain call.
- Any application contract that relies on `verify_foreign_transaction` for bridge inbound flows (the primary stated use case) is a target.

### Recommendation

The contract must verify that `response.payload_hash` commits to the `request` being resolved. Since the contract does not know the extracted values, the fix requires one of:

1. **Include extracted values in the response DTO** and have the contract recompute and verify `payload_hash = SHA256(borsh(ForeignTxSignPayload{request: request.request, values: response.values}))` before accepting the response.

2. **Embed the request hash as a domain-separation prefix** in the signed payload so the contract can verify `payload_hash` encodes `request.request` without knowing `values`. For example, `payload_hash = SHA256(SHA256(borsh(request.request)) || SHA256(borsh(values)))`, and the contract checks the first half.

3. **Enforce the binding in the contract** by requiring the node to supply the `values` alongside the signature, mirroring what `ForeignChainSignatureVerifier::verify_signature` already does in the SDK:

```rust
// In respond_verify_foreign_tx, after signature verification:
let expected_hash = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
    request: request.request.clone(),
    values: response.values.clone(), // add values to response DTO
}).compute_msg_hash()?;
if expected_hash != response.payload_hash {
    return Err(RespondError::PayloadHashMismatch.into());
}
```

### Proof of Concept

**Setup:** Two pending requests exist in `pending_verify_foreign_tx_requests`:
- `request_A`: `{domain_id: 0, payload_version: V1, request: Bitcoin{tx_id: [0xAA;32], confirmations: 1, extractors: [BlockHash]}}`
- `request_B`: `{domain_id: 0, payload_version: V1, request: Bitcoin{tx_id: [0xBB;32], confirmations: 1, extractors: [BlockHash]}}`

**Step 1:** MPC network signs `request_B`. The resulting `response_B`:
```
payload_hash = SHA256(borsh(ForeignTxSignPayload::V1{request: Bitcoin{tx_id:[0xBB;32],...}, values:[BlockHash([0xCC;32])]}))
signature    = valid_ecdsa_sig_over(payload_hash, root_key)
```

**Step 2:** Malicious attested participant calls:
```
respond_verify_foreign_tx(
    request = request_A,   // Alice's pending request
    response = response_B  // Bob's signed response
)
```

**Step 3:** Contract executes lines 718–753:
- `verify_ecdsa_signature(sig_B, payload_hash_B, root_pk)` → **Ok** (signature is genuine)
- `resolve_yields_for(&mut pending_verify_foreign_tx_requests, &request_A, serialize(response_B))` → **Ok** (request_A exists)

**Result:** Alice's promise resolves with `response_B`. Her contract receives a valid MPC signature attesting that Bitcoin `tx_id=[0xBB;32]` (Bob's transaction) has `BlockHash=[0xCC;32]` — not her `tx_id=[0xAA;32]`. The contract accepted a forged attestation. [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** crates/contract/src/lib.rs (L691-754)
```rust
    #[handle_result]
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

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L89-115)
```rust
    pub(super) async fn make_verify_foreign_tx_follower(
        &self,
        channel: NetworkTaskChannel,
        id: SignatureId,
        presignature_id: UniqueId,
    ) -> anyhow::Result<()> {
        metrics::MPC_NUM_PASSIVE_SIGN_REQUESTS_RECEIVED.inc();
        let foreign_tx_request = timeout(
            Duration::from_secs(self.config.signature.timeout_sec),
            self.verify_foreign_tx_request_store.get(id),
        )
        .await??;
        metrics::MPC_NUM_PASSIVE_SIGN_REQUESTS_LOOKUP_SUCCEEDED.inc();

        let response_payload = self
            .execute_foreign_chain_request(
                &foreign_tx_request.request,
                foreign_tx_request.payload_version,
            )
            .await?;

        let sign_request = build_signature_request(&foreign_tx_request, &response_payload)?;

        self.ecdsa_signature_provider
            .make_signature_follower_given_request(channel, presignature_id, sign_request)
            .await
    }
```
