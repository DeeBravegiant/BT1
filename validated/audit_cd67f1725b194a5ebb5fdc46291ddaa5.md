### Title
Unvalidated `payload_hash` in `respond_verify_foreign_tx` Enables Cross-Request Replay Forgery of Foreign-Chain Attestations - (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_verify_foreign_tx` verifies only that the submitted `response.payload_hash` carries a valid root-key signature. It never checks that `payload_hash` is the canonical hash of the data actually extracted from the specific `request` being answered. A single Byzantine attested participant can replay a `(payload_hash, signature)` pair from any previous legitimate foreign-tx response and attach it to a completely different pending request, causing the contract to deliver a forged attestation to the waiting caller.

---

### Finding Description

`respond_verify_foreign_tx` in `crates/contract/src/lib.rs` performs two checks before resolving the pending yield:

1. The caller is an attested participant.
2. `response.signature` is a valid ECDSA signature over `response.payload_hash` under the domain's root public key.

```rust
// crates/contract/src/lib.rs  lines 718-734
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
    ...
};
``` [1](#0-0) 

There is no third check: **the contract never verifies that `response.payload_hash` is the correct hash of the data extracted from `request`**. The `payload_hash` is supposed to be `SHA-256(borsh(ForeignTxSignPayloadV1 { request, values }))`, where `values` are the chain-observed extracted values. The contract cannot independently compute this because it has no access to the foreign chain, but it also performs no binding check whatsoever. [2](#0-1) 

Compare this with the regular `respond` function, which derives the expected public key from the request's `tweak` before verifying the signature, cryptographically binding the signature to the specific request:

```rust
// crates/contract/src/lib.rs  lines 597-598
let expected_public_key =
    derive_key_secp256k1(&affine, &request.tweak).map_err(RespondError::from)?;
``` [3](#0-2) 

No equivalent binding exists in `respond_verify_foreign_tx`. The `payload_hash` is accepted as-is from the responding node. [4](#0-3) 

---

### Impact Explanation

A single Byzantine attested participant can:

1. Observe any previously completed `respond_verify_foreign_tx` call on-chain and extract its `(payload_hash_A, signature_A)` pair. Both values are public.
2. Wait for a new pending request B (e.g., a bridge deposit verification for a different transaction).
3. Call `respond_verify_foreign_tx(request_B, { payload_hash: payload_hash_A, signature: signature_A })`.
4. The contract accepts: `signature_A` is a valid root-key signature over `payload_hash_A` (it was legitimately produced by the threshold network), and `request_B` exists in `pending_verify_foreign_tx_requests`.
5. The yield for request B is resolved with `{ payload_hash_A, signature_A }`.

The caller of request B receives an attestation that `payload_hash_A` — the hash of data from a completely different foreign transaction — is the verified result of their query. A bridge contract consuming this response (e.g., to authorize a cross-chain token release) would be presented with a forged attestation, enabling double-spend or unauthorized fund release.

The `near-mpc-sdk`'s `ForeignChainSignatureVerifier::verify_signature` does perform a `payload_hash` equality check, but only if the caller already knows the `expected_extracted_values`:

```rust
// crates/near-mpc-sdk/src/foreign_chain.rs  lines 57-63
let payload_is_correct = expected_payload_hash == response.payload_hash;
if !payload_is_correct {
    return Err(VerifyForeignChainError::IncorrectPayloadSigned { ... });
}
``` [5](#0-4) 

Bridge contracts that do not independently know the expected extracted values — which is the common case, since the whole point of the feature is to have the MPC network observe the foreign chain — cannot detect the substitution. The MPC contract is the trusted source of truth; if it delivers a forged `payload_hash`, downstream contracts have no recourse.

---

### Likelihood Explanation

- The attacker needs only one Byzantine attested participant, which is explicitly within the allowed threat model ("Byzantine participant strictly below the signing threshold").
- No threshold cooperation is required: the forged response reuses a signature already produced by the honest threshold network.
- The `(payload_hash, signature)` pairs from previous responses are permanently visible on-chain, giving the attacker an unlimited supply of valid `(hash, sig)` pairs to replay.
- The attack is executable in a single NEAR transaction with no special tooling.

---

### Recommendation

The contract must bind the `payload_hash` to the specific `request` before accepting the response. Since the contract cannot independently compute the full `payload_hash` (it lacks the `extracted_values`), the minimum viable fix is to verify that `payload_hash` commits to the correct `request` field. Concretely:

**Short term:** Require the responding node to also submit the `extracted_values` alongside the `payload_hash`. The contract recomputes `SHA-256(borsh(ForeignTxSignPayloadV1 { request, values }))` and rejects any response where the recomputed hash does not match `response.payload_hash`. This eliminates the replay vector entirely.

**Long term:** Audit all `respond_*` entry points for analogous missing binding checks. Ensure that every response is cryptographically tied to the specific request it answers, either via key derivation (as `respond` does with `tweak`) or via on-chain hash recomputation.

---

### Proof of Concept

```
// Setup: two pending foreign-tx requests exist.
// Request A (tx_id = [0x01; 32]) was already answered legitimately:
//   respond_verify_foreign_tx(request_A, { payload_hash: H_A, signature: sig_A })
//   H_A = SHA-256(borsh(ForeignTxSignPayloadV1 { request: request_A.request, values: [BlockHash([0xAA;32])] }))
//   sig_A = valid root-key ECDSA signature over H_A  (produced by the honest threshold network)

// Request B (tx_id = [0x02; 32]) is now pending — a bridge is waiting for its deposit verification.

// Attacker (single Byzantine participant) calls:
respond_verify_foreign_tx(
    request = request_B,          // correct pending request key — passes the lookup
    response = {
        payload_hash: H_A,        // hash of data from a DIFFERENT transaction
        signature:    sig_A,      // valid root-key signature over H_A — passes verify_ecdsa_signature
    }
)

// Contract accepts:
//   1. sig_A is valid over H_A under root key  ✓
//   2. request_B exists in pending_verify_foreign_tx_requests  ✓
//   3. No check that H_A == hash(request_B, ...)  ✗ (missing)

// Result: the bridge caller for request_B receives { payload_hash: H_A, signature: sig_A }.
// H_A encodes the block-hash of tx_A, not tx_B.
// The bridge contract, trusting the MPC contract's attestation, authorises a release
// based on forged foreign-chain evidence.
``` [6](#0-5) [2](#0-1) [7](#0-6)

### Citations

**File:** crates/contract/src/lib.rs (L597-598)
```rust
                let expected_public_key =
                    derive_key_secp256k1(&affine, &request.tweak).map_err(RespondError::from)?;
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

**File:** crates/near-mpc-sdk/src/foreign_chain.rs (L57-63)
```rust
        let payload_is_correct = expected_payload_hash == response.payload_hash;

        if !payload_is_correct {
            return Err(VerifyForeignChainError::IncorrectPayloadSigned {
                got: response.payload_hash.clone(),
                expected: expected_payload_hash,
            });
```

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L30-47)
```rust
fn build_signature_request(
    request: &VerifyForeignTxRequest,
    foreign_tx_payload: &dtos::ForeignTxSignPayload,
) -> anyhow::Result<SignatureRequest> {
    let payload_hash: [u8; ECDSA_PAYLOAD_SIZE_BYTES] =
        foreign_tx_payload.compute_msg_hash()?.into();
    let payload_bytes: BoundedVec<u8, ECDSA_PAYLOAD_SIZE_BYTES, ECDSA_PAYLOAD_SIZE_BYTES> =
        payload_hash.into();

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
