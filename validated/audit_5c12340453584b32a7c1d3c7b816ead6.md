### Title
Stale `payload_hash` Replay in `respond_verify_foreign_tx` Enables Forged Foreign-Chain Verification — (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_verify_foreign_tx` verifies only that the ECDSA signature in the response is valid over `response.payload_hash`, but never checks that `payload_hash` was freshly derived from the current pending `request`. A single malicious attested participant who has saved a prior threshold-signed `(payload_hash, signature)` pair can replay it against any new pending request sharing the same key, delivering stale or incorrect foreign-chain extraction data to every caller waiting on that request.

---

### Finding Description

In `respond_verify_foreign_tx` the contract performs three steps:

1. Asserts the caller is an attested participant.
2. Verifies `response.signature` over `response.payload_hash` against the **root** public key.
3. Resolves all queued yields with the raw `response` bytes. [1](#0-0) 

The `payload_hash` that the MPC network actually signs is:

```
payload_hash = SHA-256(borsh(ForeignTxSignPayload { request, values }))
```

where `values` are the extracted foreign-chain observations (block hashes, program IDs, event data, etc.). [2](#0-1) 

The contract stores only the `VerifyForeignTransactionRequest` (chain query parameters) as the pending-request key; it never stores the expected `values` or the expected `payload_hash`. [3](#0-2) 

Because `values` are unknown at request-submission time and are not committed anywhere on-chain, the contract has no basis to verify that `response.payload_hash` reflects the **current** state of the foreign chain for the given `request`. The only check performed is:

```
verify_ecdsa_signature(signature_response, &response.payload_hash, &secp_pk)
``` [4](#0-3) 

A previously produced `(payload_hash_old, signature_old)` pair — signed by the threshold network at an earlier point in time — remains cryptographically valid forever. Any single attested participant who saved that pair can submit it as the response to a new pending request that shares the same `VerifyForeignTransactionRequest` key (same `tx_id`, `extractors`, `domain_id`, `payload_version`).

Contrast this with the off-chain SDK verifier, which explicitly checks `expected_payload_hash == response.payload_hash` before accepting a response: [5](#0-4) 

The on-chain contract performs no equivalent check.

---

### Impact Explanation

The `VerifyForeignTransactionResponse` returned to every caller waiting on the replayed request contains a `payload_hash` that commits to **stale extracted values** (e.g., a block hash from before a chain reorganization, or event data from a superseded transaction). A bridge contract consuming this response via the NEAR MPC SDK's `verify_signature` helper would accept it as authentic because the ECDSA signature is genuinely valid over the stale hash.

Concrete consequences:

- **Double-spend / invalid bridge execution**: A bridge that releases funds upon receiving a verified `BlockHash` for a Bitcoin or Ethereum transaction could be tricked into releasing funds for a transaction that was reorganized out of the canonical chain, because the stale `payload_hash` commits to the pre-reorg block hash.
- **Forged foreign-chain verification**: Any downstream contract that trusts the `payload_hash` as an attestation of current foreign-chain state receives incorrect data, breaking the security guarantee the `verify_foreign_transaction` flow is designed to provide.

This matches the allowed High impact: *"Cross-chain replay, forged foreign-chain verification … that causes invalid bridge execution or double-spend conditions."*

---

### Likelihood Explanation

The attack requires only a **single malicious attested participant** (strictly below the signing threshold):

1. The participant must have been part of a prior threshold-signing session for the same `VerifyForeignTransactionRequest` key and must have saved the resulting `(payload_hash, signature)`.
2. A new pending request with the same key must exist. This is routine in bridge retry flows: if the first request's yield times out (the on-chain submission window is ~200 blocks), the bridge resubmits the identical request parameters.
3. The participant calls `respond_verify_foreign_tx` with the saved stale response before any honest node submits the fresh one.

No threshold collusion is required. The saved signature was legitimately produced by the network; the attacker merely replays it at the wrong time.

---

### Recommendation

Bind each pending request to a unique, non-replayable identifier so that a response signed for one instance cannot satisfy a different instance of the same logical request.

**Option A — Per-request nonce**: Add a monotonic nonce or the NEAR block height at submission time to `VerifyForeignTransactionRequest`. The nonce becomes part of the Borsh-serialized `ForeignTxSignPayload`, so the signed `payload_hash` is unique to each submission.

**Option B — Commit the payload hash at response time with a freshness check**: Require MPC nodes to include the NEAR block height at which they observed the foreign chain in the signed payload, and have the contract reject responses older than a configurable window.

**Option C — Pending-request epoch tag**: Store a per-request submission counter alongside each `YieldIndex` queue entry and include it in the key passed to `resolve_yields_for`, so that a response produced for epoch N cannot drain the queue created at epoch N+1.

---

### Proof of Concept

```
1. Alice submits verify_foreign_transaction(request = R) at NEAR block B₁.
   → pending_verify_foreign_tx_requests[R] = [yield_1]

2. MPC threshold network signs:
   payload_hash_old = SHA-256(borsh({request=R, values=[block_hash_before_reorg]}))
   Malicious participant P saves (payload_hash_old, sig_old).

3. The on-chain submission window expires (yield timeout ~200 blocks).
   → yield_1 is cleaned up; pending_verify_foreign_tx_requests[R] removed.

4. A foreign-chain reorganization occurs; the tx is now in a different block.

5. Alice retries: submits verify_foreign_transaction(request = R) at NEAR block B₂.
   → pending_verify_foreign_tx_requests[R] = [yield_2]

6. P calls respond_verify_foreign_tx(
       request = R,
       response = { payload_hash: payload_hash_old, signature: sig_old }
   )

7. Contract checks:
   ✓ P is an attested participant
   ✓ verify_ecdsa_signature(sig_old, payload_hash_old, root_pk) → valid
   ✓ R exists in pending_verify_foreign_tx_requests
   → resolves yield_2 with stale response

8. Alice's bridge contract receives VerifyForeignTransactionResponse{
       payload_hash: payload_hash_old,   // commits to pre-reorg block hash
       signature:    sig_old
   }
   SDK verify_signature passes (signature is genuinely valid).
   Bridge releases funds for a transaction that no longer exists on the canonical chain.
``` [6](#0-5) [7](#0-6)

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
