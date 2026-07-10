### Title
Missing `payload_hash` Content Binding in `respond_verify_foreign_tx` Enables Cross-Request Signature Replay — (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_verify_foreign_tx` verifies only that the caller-supplied `response.payload_hash` has a valid MPC signature, but never checks that `payload_hash` is the correct SHA-256 hash of `ForeignTxSignPayload{request, values}` for the pending request. A single attested participant (Byzantine, below threshold) can replay any previously observed valid `(payload_hash, signature)` pair from an earlier `respond_verify_foreign_tx` call to resolve a completely different pending request, delivering a forged foreign-chain attestation to the waiting caller.

---

### Finding Description

`respond_verify_foreign_tx` in `crates/contract/src/lib.rs` performs the following checks before resolving pending yields:

1. Caller is an attested participant.
2. The ECDSA signature in `response.signature` is valid over `response.payload_hash` using the domain's root public key.
3. The `request` key exists in `pending_verify_foreign_tx_requests`. [1](#0-0) 

What is **absent** is any binding between `response.payload_hash` and the actual content of the pending request. The contract never recomputes `SHA-256(borsh(ForeignTxSignPayload{request, values}))` and compares it to `response.payload_hash`. The hash is accepted as-is from the caller.

Contrast this with the off-chain SDK verifier `ForeignChainSignatureVerifier::verify_signature`, which explicitly enforces this binding: [2](#0-1) 

The contract-level check is structurally incomplete: it validates the signature's authenticity but not the signature's *subject matter* relative to the pending request.

The `ForeignTxSignPayload` that nodes are supposed to sign encodes both the original request and the extracted on-chain values: [3](#0-2) 

Because the contract never reconstructs this payload from the pending request and compares its hash to `response.payload_hash`, the hash field is a free variable from the contract's perspective.

---

### Impact Explanation

**Impact: High — Forged foreign-chain verification enabling invalid bridge execution.**

A single attested participant executes the following replay:

1. Observe any finalized `respond_verify_foreign_tx` transaction on-chain (NEAR is public). Extract `(payload_hash_A, sig_A)` — a valid root-key signature over `hash_A`, which encodes the data of transaction A.
2. Wait for a new `verify_foreign_transaction` request for transaction B to appear in `pending_verify_foreign_tx_requests`.
3. Call `respond_verify_foreign_tx(request=request_B, response={payload_hash=hash_A, signature=sig_A})`.
4. The contract accepts: `sig_A` is a valid root-key signature over `hash_A`. Request B exists in the pending map. Both checks pass.
5. The contract resolves all queued yields for request B, delivering `{payload_hash=hash_A, signature=sig_A}` to every waiting caller.

The callers who submitted request B receive a `VerifyForeignTransactionResponse` whose `payload_hash` encodes transaction A's data, not transaction B's. Any bridge contract that trusts the MPC contract's gating (rather than independently re-verifying the payload hash against its own expected values) will accept this forged attestation and execute bridge logic — crediting tokens, releasing funds, or advancing state — based on a transaction that was never actually verified.

The correct pending response for request B is permanently displaced: `resolve_yields_for` removes the entry from the map, so the honest MPC response can never be delivered. [4](#0-3) 

---

### Likelihood Explanation

**Likelihood: Medium.**

- The attacker must be a single attested MPC participant (Byzantine, below threshold). This is within the stated attacker model ("Byzantine participant strictly below the signing threshold").
- No threshold collusion is required. The attacker reuses a signature already produced by the honest network for a prior request.
- All `respond_verify_foreign_tx` calls are public on-chain; any participant can observe and extract `(payload_hash, signature)` pairs.
- The attack window is any time a new `verify_foreign_transaction` request is pending, which is the normal operating state of any active bridge.
- The only constraint is that the attacker must act before the honest leader submits the correct response for request B.

---

### Recommendation

In `respond_verify_foreign_tx`, after verifying the signature, recompute the expected payload hash from the pending request's content and assert it matches `response.payload_hash`. Because the contract does not store the extracted values (only the request key), the check should at minimum verify that `response.payload_hash` is a valid hash of `ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 { request: request.request.clone(), values: <any non-empty set> })`. More precisely, the contract should store the expected `payload_hash` at request submission time (computed by the submitting node) or require the responding node to include the full `ForeignTxSignPayload` so the contract can recompute and verify the hash on-chain.

A minimal short-term fix: require the responder to include the `Vec<ExtractedValue>` in the response, recompute `SHA-256(borsh(ForeignTxSignPayload{request, values}))` on-chain, and assert it equals `response.payload_hash` before accepting.

---

### Proof of Concept

```
// Step 1: Observe a prior finalized respond_verify_foreign_tx on NEAR mainnet/testnet.
// Extract from the transaction args:
//   payload_hash_A = <32-byte hash from response.payload_hash>
//   sig_A          = <K256Signature from response.signature>

// Step 2: A new verify_foreign_transaction for tx_B is pending.
// The attacker (attested participant) calls:

respond_verify_foreign_tx(
    request = VerifyForeignTransactionRequest {
        domain_id: <domain of pending request B>,
        payload_version: V1,
        request: <ForeignChainRpcRequest for tx_B>,  // must match the pending map key
    },
    response = VerifyForeignTransactionResponse {
        payload_hash: payload_hash_A,   // hash of tx_A's data, NOT tx_B's
        signature: sig_A,               // valid root-key signature over payload_hash_A
    },
)

// Contract flow:
// 1. assert_caller_is_attested_participant_and_protocol_active() → passes (attacker is attested)
// 2. verify_ecdsa_signature(sig_A, payload_hash_A, root_pk) → passes (sig_A is valid)
// 3. resolve_yields_for(pending_verify_foreign_tx_requests, request_B, response) → resolves
//    all queued yields for tx_B with the forged response.
//
// Result: callers waiting on tx_B receive {payload_hash_A, sig_A}.
// The pending entry for tx_B is removed; the honest response can never arrive.
``` [5](#0-4) [6](#0-5)

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
