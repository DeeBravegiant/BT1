### Title
`respond_verify_foreign_tx` Accepts Replayed Responses Without Request-to-Payload Binding Verification — (File: `crates/contract/src/lib.rs`)

---

### Summary

The `respond_verify_foreign_tx` function verifies that the submitted signature is cryptographically valid over the caller-supplied `response.payload_hash`, but never verifies that `response.payload_hash` is actually the canonical hash of the payload derived from the pending `request` (i.e., `SHA-256(borsh(ForeignTxSignPayload::V1 { request: request.request, values: ... }))`). A single Byzantine attested participant strictly below the signing threshold can replay any previously observed, legitimately produced `VerifyForeignTransactionResponse` to resolve an entirely different pending request, delivering a forged verification attestation to the waiting caller.

---

### Finding Description

`verify_foreign_transaction` enqueues a yield-resume promise keyed on a `VerifyForeignTransactionRequest` (containing `domain_id`, `payload_version`, and the `ForeignChainRpcRequest` with `tx_id`, `extractors`, etc.). The MPC nodes are expected to inspect the foreign chain, extract values, compute `payload_hash = SHA-256(borsh(ForeignTxSignPayload::V1 { request, values }))`, sign it, and call `respond_verify_foreign_tx`.

The contract-side check in `respond_verify_foreign_tx` is:

```rust
// crates/contract/src/lib.rs  lines 718–747
let payload_hash: [u8; 32] = response.payload_hash.0;

near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,
)
.is_ok()
```

The contract confirms only that `response.signature` is a valid MPC signature over `response.payload_hash`. It does **not** verify that `response.payload_hash` equals `SHA-256(borsh(ForeignTxSignPayload::V1 { request: request.request, values: <anything> }))` for the specific `request` being resolved.

By contrast, the regular `respond` path for `sign()` requests binds the signature to the payload stored inside the `SignatureRequest` itself, so no analogous decoupling exists there.

The `args_into_verify_foreign_tx_request` conversion confirms the request key carries no payload hash:

```rust
// crates/contract/src/dto_mapping.rs  lines 840–848
pub fn args_into_verify_foreign_tx_request(
    args: dtos::VerifyForeignTransactionRequestArgs,
) -> dtos::VerifyForeignTransactionRequest {
    dtos::VerifyForeignTransactionRequest {
        domain_id: args.domain_id,
        request: args.request,
        payload_version: args.payload_version,
    }
}
```

No `payload_hash` field is stored in the pending map entry; the contract has no on-chain reference to compare against.

---

### Impact Explanation

A Byzantine attested participant (strictly below the signing threshold) can:

1. Observe any previously finalized `respond_verify_foreign_tx` call on-chain for transaction Y, obtaining `{payload_hash_Y, signature_Y}` — both are public.
2. Wait for (or trigger) a new `verify_foreign_transaction` request for a different transaction X from a victim bridge contract.
3. Call `respond_verify_foreign_tx` supplying `request` = the pending request for tx X, but `response = {payload_hash_Y, signature_Y}`.
4. The contract passes all checks: the request for tx X is in the pending map, and `signature_Y` is a valid MPC signature over `payload_hash_Y`.
5. The yield for tx X is resolved with the forged response; the victim bridge receives `{payload_hash_Y, signature_Y}`.

A downstream bridge that does not independently re-derive the expected payload hash from the original `ForeignChainRpcRequest` and compare it to `response.payload_hash` will accept this as proof that tx X was verified. This enables double-spend or invalid bridge execution: the attacker can, for example, use the attestation for a small/already-spent transaction Y to authorize a large release tied to transaction X.

The `near-mpc-sdk` does provide `ForeignChainSignatureVerifier::verify_signature` which performs this check client-side, but the MPC contract — the authoritative source of truth — does not enforce it, leaving every bridge that omits the SDK check fully exposed.

---

### Likelihood Explanation

Medium. The attacker must be an attested MPC participant (below the signing threshold), which is an explicit in-scope role per the audit brief. No threshold collusion is required: a single Byzantine node can call `respond_verify_foreign_tx` unilaterally. The only prerequisite is the existence of any prior legitimate response on-chain for any transaction on the same domain — a condition trivially satisfied in production. The attacker does not need to forge a new MPC signature; replaying a public, already-valid one suffices.

---

### Recommendation

The contract should enforce the binding between the pending request and the response's payload hash. The simplest fix is to require the responder to include the extracted `values` in the response, and have the contract recompute and compare:

```rust
let expected_hash = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
    request: request.request.clone(),
    values: response.extracted_values.clone(),
}).compute_msg_hash()?;

if expected_hash != response.payload_hash {
    return Err(RespondError::PayloadHashMismatch.into());
}
```

Alternatively, the contract could store the expected payload hash at enqueue time if the caller is required to commit to the extracted values upfront, though this changes the protocol semantics. At minimum, the `respond_verify_foreign_tx` path should mirror the binding guarantee already present in the `respond` path for regular `sign()` requests.

---

### Proof of Concept

```
// Step 1 – Attacker (Byzantine attested node) observes a past on-chain response
//           for Bitcoin tx_id = Y:
//   payload_hash_Y = SHA-256(borsh(ForeignTxSignPayload::V1 { request: BTC/Y, values: [BlockHash(0xAA..)] }))
//   signature_Y    = valid MPC ECDSA sig over payload_hash_Y

// Step 2 – Victim bridge submits:
contract.verify_foreign_transaction({
    domain_id: ForeignTx_domain,
    payload_version: V1,
    request: ForeignChainRpcRequest::Bitcoin { tx_id: X, confirmations: 6, extractors: [BlockHash] }
});
// → pending_verify_foreign_tx_requests[request_X] = [yield_id_for_victim]

// Step 3 – Attacker calls (as an attested participant):
contract.respond_verify_foreign_tx(
    request  = request_X,          // the pending request for tx X
    response = { payload_hash: payload_hash_Y, signature: signature_Y }
);
// Contract checks:
//   ✓ caller is attested participant
//   ✓ verify_ecdsa_signature(signature_Y, payload_hash_Y, mpc_pubkey) == Ok
//   ✓ request_X is in pending map
// → resolves yield_id_for_victim with { payload_hash_Y, signature_Y }

// Step 4 – Victim bridge receives the response and (if it skips SDK re-verification)
//           treats payload_hash_Y as proof that tx X was verified.
//           Attacker can now claim the bridge payout for tx X using the attestation
//           originally produced for the unrelated tx Y.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** crates/contract/src/dto_mapping.rs (L840-848)
```rust
pub fn args_into_verify_foreign_tx_request(
    args: dtos::VerifyForeignTransactionRequestArgs,
) -> dtos::VerifyForeignTransactionRequest {
    dtos::VerifyForeignTransactionRequest {
        domain_id: args.domain_id,
        request: args.request,
        payload_version: args.payload_version,
    }
}
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L107-128)
```rust
#[derive(
    Debug,
    Clone,
    Eq,
    PartialEq,
    Ord,
    PartialOrd,
    Hash,
    Serialize,
    Deserialize,
    BorshSerialize,
    BorshDeserialize,
)]
#[cfg_attr(
    all(feature = "abi", not(target_arch = "wasm32")),
    derive(schemars::JsonSchema, borsh::BorshSchema)
)]
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
