### Title
`respond_verify_foreign_tx` Does Not Bind `response.payload_hash` to the Stored Request — (`File: crates/contract/src/lib.rs`)

---

### Summary

`respond_verify_foreign_tx` accepts a `request` and a `response` parameter. The contract uses `request` only as a map-lookup key to find the pending yield, then verifies that `response.signature` is a valid MPC signature over `response.payload_hash`. It never checks that `response.payload_hash` is the hash that should have been produced for that specific `request`. A single malicious MPC leader — strictly below the signing threshold — can obtain a legitimately-produced threshold signature for one pending foreign-tx request and replay it as the response to a *different* pending request, delivering a forged verification attestation to the waiting bridge contract.

---

### Finding Description

In `respond_verify_foreign_tx` the contract performs two independent operations:

1. **Signature check** — verifies `response.signature` over `response.payload_hash` using the domain's root public key.
2. **State resolution** — calls `pending_requests::resolve_yields_for` keyed on `request`. [1](#0-0) 

The `request` parameter is never used to *constrain* `response.payload_hash`. The contract cannot recompute the expected hash because the extracted values (`ForeignTxSignPayloadV1::values`) are determined off-chain by the MPC nodes and are not stored on-chain. [2](#0-1) 

Contrast this with the regular `respond` function, where the payload hash is taken directly from the stored `request.payload` — the contract never trusts a caller-supplied hash: [3](#0-2) 

In `respond_verify_foreign_tx` the hash comes from `response.payload_hash`, a field supplied by the responding node, with no binding to the stored request.

The node-side `build_signature_request` shows that the hash signed by the MPC network is `SHA-256(borsh(ForeignTxSignPayload::V1 { request, values }))`: [4](#0-3) 

Because `request` (the `ForeignChainRpcRequest`) is part of the pre-image, two different pending requests produce two different hashes. A signature produced for request A is cryptographically valid over `hash_of_A`, and the contract will accept it as a response to request B as long as the signature verifies — which it does, because the MPC root key was used.

---

### Impact Explanation

A malicious MPC leader resolves a victim's pending `verify_foreign_transaction` request with a `payload_hash` that corresponds to a *different* foreign-chain transaction. The victim bridge contract receives a `VerifyForeignTransactionResponse` whose `payload_hash` and `signature` are internally consistent (the signature is valid over the hash) but whose hash does not encode the transaction the bridge actually asked about.

Bridge contracts that rely on the on-chain response to gate fund releases or state transitions — without independently recomputing the expected hash from the known request and the returned values — will accept the forged attestation. This enables invalid bridge execution: funds can be released for a transaction that was never verified, or a transaction that was verified for a different chain/tx-id is presented as proof for the target transaction.

The `near-mpc-sdk` `ForeignChainSignatureVerifier::verify_signature` does perform this check client-side: [5](#0-4) 

However, this check is not enforced by the contract, and bridge contracts that do not use the SDK helper, or that trust `payload_hash` directly, are fully exposed.

**Allowed impact match:** High — forged foreign-chain verification causing invalid bridge execution.

---

### Likelihood Explanation

- Requires exactly **one** Byzantine MPC node to be elected leader for any signing round — strictly below the signing threshold.
- The attacker does not need to manipulate the threshold signing protocol; they simply retain the legitimately-produced signature from round A and submit it as the response to round B.
- The attacker can submit their own `verify_foreign_transaction` request (request A) to guarantee a signing round they lead, then target any concurrently pending request B submitted by a bridge contract.
- No special privileges beyond being an active, attested MPC participant are required.

---

### Recommendation

**Short term:** Document explicitly that callers of `verify_foreign_transaction` **must** verify `response.payload_hash` against the hash they reconstruct from the known request and the returned extracted values before acting on the response. Require use of `ForeignChainSignatureVerifier::verify_signature` from the SDK.

**Long term:** Redesign the response to include the full `ForeignTxSignPayload` (not just the hash), so the contract — or at minimum the caller — can independently recompute and compare the hash. Alternatively, store a commitment to the expected hash on-chain at request submission time (e.g., a hash of the request alone, with the node-supplied values verified against a bounded set of allowed extractor outputs), enabling the contract to enforce the binding in `respond_verify_foreign_tx`.

---

### Proof of Concept

1. **Setup:** Two pending requests exist on-chain:
   - Request A: `BitcoinRpcRequest { tx_id: [0xAA; 32], confirmations: 1, extractors: [BlockHash] }`
   - Request B: `BitcoinRpcRequest { tx_id: [0xBB; 32], confirmations: 1, extractors: [BlockHash] }` — submitted by a bridge contract.

2. **Eve** (malicious MPC node) is elected leader for request A's signing round.

3. Honest followers independently query Bitcoin for tx `0xAA`, extract `block_hash_A`, and sign `hash_of_A = SHA-256(borsh(V1 { request: A, values: [block_hash_A] }))`. Eve collects the threshold signature `sig_over_hash_of_A`.

4. Eve calls:
   ```
   respond_verify_foreign_tx(
     request = VerifyForeignTransactionRequest { request: B, domain_id, payload_version },
     response = VerifyForeignTransactionResponse {
       payload_hash: hash_of_A,
       signature: sig_over_hash_of_A,
     }
   )
   ```

5. The contract:
   - Looks up request B in `pending_verify_foreign_tx_requests` → **found** (line 749–753).
   - Verifies `sig_over_hash_of_A` over `hash_of_A` against the root public key → **valid** (line 729–734).
   - Resolves request B's yield with `response = { payload_hash: hash_of_A, sig_over_hash_of_A }`.

6. The bridge contract receives a response whose `payload_hash` encodes tx `0xAA` and `block_hash_A`, not tx `0xBB`. If the bridge contract does not call `ForeignChainSignatureVerifier::verify_signature` with the expected values for tx `0xBB`, it accepts the forged attestation and proceeds as if tx `0xBB` was verified. [6](#0-5) [2](#0-1) [4](#0-3)

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

**File:** crates/near-mpc-sdk/src/foreign_chain.rs (L53-64)
```rust
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
