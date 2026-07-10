### Title
Cross-Request Signature Replay in `respond_verify_foreign_tx` Delivers Forged Foreign-Chain Verification Results — (File: `crates/contract/src/lib.rs`)

### Summary

`respond_verify_foreign_tx` verifies that the submitted ECDSA signature is valid over `response.payload_hash` using the root public key, and that `request` exists in the pending queue — but it never verifies that `response.payload_hash` actually encodes the same `ForeignChainRpcRequest` that was submitted. A single malicious attested participant (below threshold) can replay any previously published threshold signature for transaction Y as the response to a pending request for transaction X, causing the contract to deliver a forged verification result to the caller.

### Finding Description

In `respond_verify_foreign_tx`, the signature check is:

```rust
let payload_hash: [u8; 32] = response.payload_hash.0;
// Check the signature is correct against the root public key
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,          // root key — no tweak
)
.is_ok()
``` [1](#0-0) 

After this check passes, the contract resolves all yields queued under `request`:

```rust
pending_requests::resolve_yields_for(
    &mut self.pending_verify_foreign_tx_requests,
    &request,
    serde_json::to_vec(&response).unwrap(),
)
``` [2](#0-1) 

The two checks are entirely independent: the contract verifies that `(signature, payload_hash)` is a valid root-key signature pair, and separately that `request` is in the pending map. It never checks that `payload_hash` is `SHA-256(borsh(ForeignTxSignPayload::V1 { request: request.request, values: ... }))` — i.e., that the hash actually commits to the same `ForeignChainRpcRequest` that was submitted.

The canonical payload structure is:

```rust
pub struct ForeignTxSignPayloadV1 {
    pub request: ForeignChainRpcRequest,   // tx_id, extractors, finality
    pub values: Vec<ExtractedValue>,
}
impl ForeignTxSignPayload {
    pub fn compute_msg_hash(&self) -> std::io::Result<Hash256> {
        let mut hasher = sha2::Sha256::new();
        borsh::BorshSerialize::serialize(self, &mut hasher)?;
        Ok(Hash256(hasher.finalize().into()))
    }
}
``` [3](#0-2) 

Every `respond_verify_foreign_tx` call is published on-chain. Its `response.payload_hash` and `response.signature` are permanently visible. A malicious attested participant can extract any previously published `(payload_hash_Y, signature_Y)` pair and submit it as the response to a completely different pending request `request_X`.

Contrast with `respond` (regular sign), which derives the expected public key from the stored tweak before verifying:

```rust
let expected_public_key =
    derive_key_secp256k1(&affine, &request.tweak).map_err(RespondError::from)?;
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    payload_hash,
    &expected_public_key,
)
``` [4](#0-3) 

For `respond`, the tweak is derived from the caller's account and derivation path and is baked into the pending-request key, so a replayed signature for a different caller/path would fail key derivation. `respond_verify_foreign_tx` has no equivalent binding — the root key is used unconditionally and `payload_hash` is caller-supplied.

The node side confirms the zero-tweak design:

```rust
Ok(SignatureRequest {
    ...
    tweak: Tweak::new([0u8; 32]),   // zero tweak → root key
    ...
})
``` [5](#0-4) 

Because the root key is shared across all foreign-tx requests, every legitimately issued `(payload_hash, signature)` pair is a valid credential that can be replayed against any pending request.

### Impact Explanation

A bridge contract calls `verify_foreign_transaction(request_X)` to confirm that a specific foreign-chain deposit transaction (tx_id=X) finalized before releasing funds. The contract returns `VerifyForeignTransactionResponse { payload_hash, signature }`. If a malicious attested participant replays `(payload_hash_Y, signature_Y)` — a response previously issued for a different transaction (tx_id=Y) — the bridge receives a cryptographically valid response whose `payload_hash` commits to tx_id=Y, not tx_id=X. Any bridge contract that does not independently recompute and compare `payload_hash` against its own expected values (i.e., does not use `ForeignChainSignatureVerifier::verify_signature` from the SDK) will accept this as proof that tx_id=X was verified and release funds without a matching deposit. This is a forged foreign-chain verification enabling invalid bridge execution. [6](#0-5) 

### Likelihood Explanation

The attacker is a single malicious attested participant — strictly below the signing threshold. No threshold collusion is required. The attacker needs only:

1. A previously published `(payload_hash_Y, signature_Y)` pair from any past `respond_verify_foreign_tx` call (publicly visible on-chain).
2. A pending `verify_foreign_transaction(request_X)` request (which the attacker can submit themselves for 1 yoctoNEAR).
3. Attested participant status (required to call `respond_verify_foreign_tx`).

The attacker does not need to forge a new threshold signature. The replay uses an already-valid signature. The attack is repeatable for any pending request and any previously issued response.

### Recommendation

Inside `respond_verify_foreign_tx`, after verifying the signature, recompute the expected payload hash from `request.request` and the extracted values encoded in `response.payload_hash`, and assert they match. Concretely, the contract should require that `response.payload_hash` is a valid `ForeignTxSignPayload::V1` hash whose embedded `ForeignChainRpcRequest` equals `request.request`. Because the contract does not store the extracted values, the simplest fix is to require the responder to also supply the `ForeignTxSignPayload` (not just its hash), compute the hash on-chain, and verify the signature over the computed hash. This eliminates the ability to substitute an arbitrary `payload_hash`.

Alternatively, bind the signature to the request by including a request-specific nonce (e.g., the NEAR receipt ID of the original `verify_foreign_transaction` call) in the signed payload, making cross-request replay cryptographically impossible.

### Proof of Concept

```
1. Alice submits verify_foreign_transaction(request_X) for tx_id=X (deposit of 100 ETH).
   → pending_verify_foreign_tx_requests[request_X] = [yield_alice]

2. Separately, the MPC network legitimately processes verify_foreign_transaction(request_Y)
   for tx_id=Y (a 1 ETH deposit). The honest leader calls:
     respond_verify_foreign_tx(request_Y, {payload_hash_Y, signature_Y})
   This is published on-chain and visible to all.

3. Malicious attested participant M extracts (payload_hash_Y, signature_Y) from chain history.

4. M calls respond_verify_foreign_tx(request_X, {payload_hash_Y, signature_Y}).

5. Contract checks:
   - request_X ∈ pending_verify_foreign_tx_requests  ✓
   - verify_ecdsa_signature(signature_Y, payload_hash_Y, root_key)  ✓  (valid, reused)
   - (no check that payload_hash_Y encodes request_X.request)

6. Contract resolves yield_alice with {payload_hash_Y, signature_Y}.

7. Alice's bridge contract receives a cryptographically valid response.
   If it does not recompute and compare payload_hash against its expected hash for tx_id=X,
   it releases 100 ETH to the attacker based on a 1 ETH deposit verification.
``` [7](#0-6) [8](#0-7)

### Citations

**File:** crates/contract/src/lib.rs (L597-608)
```rust
                let expected_public_key =
                    derive_key_secp256k1(&affine, &request.tweak).map_err(RespondError::from)?;

                let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");

                // Check the signature is correct
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    payload_hash,
                    &expected_public_key,
                )
                .is_ok()
```

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

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L124-128)
```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
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

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L39-47)
```rust
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
