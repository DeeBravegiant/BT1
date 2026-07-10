### Title
`respond_verify_foreign_tx` Accepts Arbitrary `payload_hash` Without Validating It Against the Original Request - (File: `crates/contract/src/lib.rs`)

### Summary

`respond_verify_foreign_tx` verifies that the submitted signature is valid over the caller-supplied `response.payload_hash`, but never checks that `payload_hash` is the correct hash for the original `request`. A single Byzantine attested participant can replay any previously produced valid threshold signature (publicly visible on-chain) as a response to a different pending request, causing the contract to resolve the yield with a forged `payload_hash` and deliver a fabricated foreign-chain verification result to the user.

### Finding Description

The vulnerability class from M-18 is: a parameter is **modified/introduced during execution** but dependent validation checks are **not re-run against the new value**. In M-18, the price was changed from the original limit price to the current market price, but SL/TP were not re-validated against the new price.

The exact analog here is: `payload_hash` is a **new value introduced during execution** (computed by MPC nodes from foreign-chain data; it is not present in the original `verify_foreign_transaction` request). The contract validates the signature over this new value but does **not re-validate the new value against the original request parameters**.

In `respond_verify_foreign_tx`:

```rust
let payload_hash: [u8; 32] = response.payload_hash.0;

// Check the signature is correct against the root public key
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,
)
.is_ok()
```

The contract only checks that `response.signature` is a valid ECDSA signature over `response.payload_hash` under the root public key. It does **not** check that `response.payload_hash` equals `SHA-256(borsh(ForeignTxSignPayload { request, extracted_values }))` for the specific `request` being resolved.

The correct hash is defined as:

```rust
pub struct ForeignTxSignPayloadV1 {
    pub request: ForeignChainRpcRequest,  // must match the pending request
    pub values: Vec<ExtractedValue>,       // foreign-chain data
}
// msg_hash = SHA-256(borsh(ForeignTxSignPayload))
```

Because `request` is embedded in the signed payload, a signature produced for request A (`payload_hash_A`) is cryptographically distinct from the correct hash for request B (`payload_hash_B`). The contract never enforces this binding. [1](#0-0) [2](#0-1) [3](#0-2) 

### Impact Explanation

A Byzantine attested participant replays a previously produced valid threshold signature (from any prior foreign-tx request, publicly visible on-chain) as a response to a different pending request. The contract resolves the yield with an incorrect `payload_hash`, delivering a fabricated `VerifyForeignTransactionResponse` to the user's contract. Any user contract that does not independently re-derive and compare the expected `payload_hash` (i.e., trusts the MPC contract to return the correct hash) will accept the forged verification result. This enables **forged foreign-chain verification** and can cause **invalid bridge execution or double-spend conditions** — for example, a bridge contract accepting proof that transaction 0xBB was verified when the signed data actually corresponds to transaction 0xAA.

This maps to the allowed High impact: *"Cross-chain replay, forged foreign-chain verification, light-client-style verification bypass, or participant/attestation authorization bypass that causes invalid bridge execution or double-spend conditions."* [4](#0-3) [5](#0-4) 

### Likelihood Explanation

The attack requires only a **single Byzantine attested participant** — strictly below the signing threshold. No key-share forgery or threshold collusion is needed. The attacker:

1. Observes any previously completed `respond_verify_foreign_tx` call on-chain (the `{payload_hash_A, signature_A}` pair is public).
2. Waits for any new `verify_foreign_transaction` request B to enter the pending queue.
3. Calls `respond_verify_foreign_tx(request_B, {payload_hash_A, signature_A})`.

The `assert_caller_is_attested_participant_and_protocol_active` check is satisfied because the attacker is a legitimate attested participant. The signature check passes because `signature_A` is a valid threshold signature over `payload_hash_A`. The pending-request lookup passes because `request_B` is genuinely pending. [6](#0-5) 

### Recommendation

The contract must bind `payload_hash` to the original `request`. Since the contract cannot query the foreign chain, the recommended fix is to require the response to include the full `ForeignTxSignPayload` (not just the hash), so the contract can:

1. Verify that `payload.request == request` (the embedded request matches the pending request).
2. Recompute `expected_hash = payload.compute_msg_hash()`.
3. Verify that `response.payload_hash == expected_hash`.
4. Verify that `response.signature` is valid over `expected_hash`.

This mirrors the M-18 mitigation: re-validate the dependent constraint (payload binding) against the new value (payload_hash) before accepting the response. [7](#0-6) 

### Proof of Concept

```
1. Request A: verify_foreign_transaction(Bitcoin tx_id=0xAA) is processed.
   → MPC nodes sign payload_hash_A = SHA256(borsh({request_A, [BlockHash=0x11...]}))
   → respond_verify_foreign_tx(request_A, {payload_hash_A, signature_A}) is submitted on-chain.
   → signature_A is now publicly visible.

2. Request B: verify_foreign_transaction(Bitcoin tx_id=0xBB) is submitted by user B.
   → request_B enters pending_verify_foreign_tx_requests.

3. Byzantine attested participant calls:
   respond_verify_foreign_tx(request_B, {payload_hash_A, signature_A})

4. Contract checks (lib.rs:718-734):
   verify_ecdsa_signature(signature_A, payload_hash_A, root_pk) → PASSES
   (No check that payload_hash_A corresponds to request_B)

5. pending_requests::resolve_yields_for resolves request_B with {payload_hash_A, signature_A}.

6. User B's contract receives VerifyForeignTransactionResponse {
       payload_hash: payload_hash_A,  // hash of tx_id=0xAA data, NOT tx_id=0xBB
       signature: signature_A,        // valid signature over payload_hash_A
   }

7. User B's contract, trusting the MPC contract, accepts this as proof that tx_id=0xBB
   was verified — enabling invalid bridge execution.
``` [8](#0-7) [9](#0-8)

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

**File:** crates/node/src/mpc_client.rs (L595-601)
```rust
                                        let payload_hash = response.0.0.compute_msg_hash()?;
                                        let response = contract_args::VerifyForeignTransactionRespondArgs::from_signature(
                                            verify_foreign_tx_attempt.request.clone(),
                                            payload_hash,
                                            response.0.1,
                                            response.1,
                                        )?;
```
