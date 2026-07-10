### Title
Missing `payload_hash`-to-`request` Binding in `respond_verify_foreign_tx` Enables Cross-Request Signature Replay by a Single Byzantine Participant - (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_verify_foreign_tx` verifies that the submitted ECDSA signature is valid over `response.payload_hash` using the root MPC public key, but **never checks that `payload_hash` was derived from the specific `request` being responded to**. A single Byzantine attested participant can replay a valid `(payload_hash, signature)` pair observed from any previously completed `respond_verify_foreign_tx` call to satisfy a different pending request, delivering a forged foreign-chain verification result to the user.

---

### Finding Description

The `respond_verify_foreign_tx` function in `crates/contract/src/lib.rs` performs the following checks:

1. Caller is an attested participant (`assert_caller_is_attested_participant_and_protocol_active`)
2. Protocol is running or resharing
3. `accept_requests` is true
4. The ECDSA signature in `response.signature` is valid over `response.payload_hash` using the root MPC public key [1](#0-0) 

What is **never checked** is whether `response.payload_hash` was actually derived from the `request` argument. The correct hash is defined as:

```
payload_hash = SHA256(borsh(ForeignTxSignPayload::V1 { request: request.request, values: <extracted_values> }))
``` [2](#0-1) 

The `values` (extracted values from the foreign chain) are embedded inside `payload_hash` but are never provided to the contract. The contract therefore cannot recompute the expected hash. It blindly trusts the caller-supplied `payload_hash` as long as the signature over it is valid under the root key.

The SDK's `ForeignChainSignatureVerifier::verify_signature` — a **client-side** helper — does perform this binding check: [3](#0-2) 

But this check is not enforced by the contract itself.

---

### Impact Explanation

A single Byzantine attested participant can execute the following cross-request replay:

1. **Observe** a completed `respond_verify_foreign_tx` call on-chain for request A (e.g., Bitcoin tx `[0x01;32]`). The `(payload_hash_A, signature_A)` pair is publicly visible in the transaction.
2. **Wait** for a different request B (e.g., Bitcoin tx `[0x02;32]`) to be submitted and appear in `pending_verify_foreign_tx_requests`.
3. **Submit** `respond_verify_foreign_tx(request_B, { payload_hash: payload_hash_A, signature: signature_A })`.
4. The contract verifies: `signature_A` is valid over `payload_hash_A` under the root key ✓. It then calls `resolve_yields_for` for request B, delivering `{ payload_hash_A, signature_A }` to the user who submitted request B.

The user's NEAR contract receives a `VerifyForeignTransactionResponse` whose `payload_hash` encodes the verification of a **different** foreign transaction (request A's tx_id and extracted values), not the one they requested. Any bridge or application logic that trusts this response without independently recomputing the expected hash (using the SDK helper) will act on a forged attestation — e.g., releasing bridge funds based on a transaction that was never actually verified.

This matches the allowed impact: **"Cross-chain replay, forged foreign-chain verification … that causes invalid bridge execution or double-spend conditions."**

---

### Likelihood Explanation

- The attacker is a **single attested participant** — strictly below the signing threshold. No collusion is required.
- The replayed `(payload_hash, signature)` pair is obtained from **public on-chain data** (any past `respond_verify_foreign_tx` transaction). No key material or privileged access is needed beyond being an attested participant.
- In production, multiple `verify_foreign_transaction` requests are pending simultaneously, giving the attacker a pool of targets.
- The attack is silent: the contract emits no error, the yield resolves normally, and the user receives a well-formed (but forged) response.

---

### Recommendation

The contract must enforce that `response.payload_hash` is bound to the specific `request`. This requires including the extracted `values` in the response so the contract can recompute and verify the hash:

```rust
// In respond_verify_foreign_tx, after signature verification:
let expected_hash = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
    request: request.request.clone(),
    values: response.values.clone(), // values must be added to VerifyForeignTransactionResponse
})
.compute_msg_hash()
.map_err(|_| RespondError::InvalidPayloadHash)?;

if expected_hash != response.payload_hash {
    return Err(RespondError::PayloadHashMismatch.into());
}
```

Alternatively, the `VerifyForeignTransactionResponse` struct must be extended to include the extracted `values`, and the contract must perform this recomputation before accepting the response.

---

### Proof of Concept

```
// Setup: request A has been completed on-chain.
// payload_hash_A = SHA256(borsh(ForeignTxSignPayload::V1 { request: bitcoin_tx_A, values: [BlockHash([0x42;32])] }))
// sig_A = valid ECDSA signature over payload_hash_A under root MPC key

// Attacker (single Byzantine attested participant) observes payload_hash_A and sig_A from chain.

// Request B is now pending:
let request_b = VerifyForeignTransactionRequest {
    domain_id: ...,
    payload_version: ForeignTxPayloadVersion::V1,
    request: ForeignChainRpcRequest::Bitcoin(BitcoinRpcRequest {
        tx_id: [0x02; 32].into(), // different tx
        ...
    }),
};

// Attacker submits replayed response for request B:
contract.respond_verify_foreign_tx(
    request_b,
    VerifyForeignTransactionResponse {
        payload_hash: payload_hash_A,  // hash of a DIFFERENT request
        signature: sig_A,              // valid signature, but over wrong payload
    }
);
// Contract accepts: sig_A is valid over payload_hash_A under root key.
// User who submitted request B receives payload_hash_A — a forged attestation.
``` [4](#0-3) [5](#0-4)

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
