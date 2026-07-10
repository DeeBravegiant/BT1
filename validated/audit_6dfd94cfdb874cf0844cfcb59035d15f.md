### Title
`respond_verify_foreign_tx` Accepts Caller-Supplied `payload_hash` Without Binding It to the Stored Request — Cross-Request Signature Replay Enables Forged Foreign-Chain Verification - (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_verify_foreign_tx` verifies that the submitted ECDSA signature is valid over `response.payload_hash`, but `payload_hash` is taken directly from the node-supplied response rather than being derived from the on-chain stored request. A single Byzantine attested participant (strictly below the signing threshold) can replay a valid signature obtained from a prior signing round to resolve a completely different pending foreign-tx request with an incorrect `payload_hash`, causing the contract to deliver a forged verification result to the waiting caller.

---

### Finding Description

The contract stores pending `verify_foreign_transaction` requests in `pending_verify_foreign_tx_requests` keyed by the full `VerifyForeignTransactionRequest` struct (`domain_id`, `payload_version`, `request`). When a node calls `respond_verify_foreign_tx`, the contract performs the following check:

```rust
// crates/contract/src/lib.rs:726-734
let payload_hash: [u8; 32] = response.payload_hash.0;

// Check the signature is correct against the root public key
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,
)
.is_ok()
```

`payload_hash` is taken from `response` — a caller-controlled argument — and is never cross-checked against the stored request. [1](#0-0) 

Compare this with the regular `respond` function, which correctly derives the message to verify from the **stored** request:

```rust
// crates/contract/src/lib.rs:600-607
let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    payload_hash,
    &expected_public_key,
)
``` [2](#0-1) 

The intended design is that nodes compute `payload_hash = SHA-256(borsh(ForeignTxSignPayload { request, values }))` where `values` are the values extracted from the foreign chain RPC, then sign it. The contract cannot recompute this hash because it does not store `values`. However, the contract also performs **no binding check** between `response.payload_hash` and the `request` key used for the lookup. Any hash that carries a valid root-key signature is accepted. [3](#0-2) 

The `VerifyForeignTransactionResponse` type confirms that `payload_hash` is a free field in the response DTO: [4](#0-3) 

The SDK-side verifier (`ForeignChainSignatureVerifier::verify_signature`) does perform the binding check — it recomputes the expected hash from the request and the caller-supplied `expected_extracted_values` and asserts equality with `response.payload_hash`. But this check lives entirely off-chain in the SDK, not in the contract. [5](#0-4) 

---

### Impact Explanation

**Impact: High — Forged foreign-chain verification / invalid bridge execution.**

A Byzantine participant can resolve a pending `verify_foreign_transaction` request for foreign-chain transaction R2 with a `payload_hash` that actually encodes the extracted values for a completely different transaction R1. The contract accepts the response, removes R2 from the pending map, and delivers `{ payload_hash: hash(R1, values1), signature_over_hash(R1,values1) }` to the user who submitted R2.

Any bridge contract that:
1. Calls `verify_foreign_transaction` to obtain a signed attestation of on-chain state, and
2. Verifies only that the returned signature is valid over the returned `payload_hash` (without independently recomputing the expected hash from the request and the expected extracted values)

will accept this forged attestation as proof that R2's foreign-chain state matches `values1` (which it does not). This enables double-spend or invalid bridge execution: e.g., an attacker can make a bridge contract believe a Bitcoin transaction was confirmed in block B1 when it was actually confirmed in block B2, or that a different set of log values was extracted from an Ethereum transaction.

Even for users who do use the SDK, the pending request is permanently consumed — the yield is resolved and removed from the map — so the legitimate response can never be delivered. This is an irreversible denial of the correct result.

---

### Likelihood Explanation

**Likelihood: High.**

- The attacker must be a single **attested participant** (not threshold-many). The `assert_caller_is_attested_participant_and_protocol_active` check requires only that the caller is one of the current participants with a valid TEE attestation. [6](#0-5) 
- The attacker obtains a valid root-key signature by participating in any prior `verify_foreign_transaction` signing round. After the threshold protocol completes, every participating node holds the full signature. The attacker simply saves it.
- The attacker observes pending requests on-chain via the NEAR indexer (this is the normal operating mode for all nodes). [7](#0-6) 
- No new cryptographic capability is needed: the attacker reuses an existing valid signature over a different hash.
- The attack requires no collusion with other participants and no privileged access beyond being an attested participant.

---

### Recommendation

**Short term:** In `respond_verify_foreign_tx`, add a binding check that ensures `response.payload_hash` is consistent with the stored request. Since the contract cannot know `extracted_values`, the minimum viable fix is to verify that the `request` field embedded in the hash matches the stored request. One approach: require the node to also submit the `extracted_values` alongside the response, recompute `SHA-256(borsh(ForeignTxSignPayload { request, values }))` on-chain, and assert it equals `response.payload_hash` before accepting the response.

**Long term:** Audit all `respond*` entry points to ensure the message verified by the contract is always derived from on-chain stored state, never from caller-supplied response fields. The `respond` and `respond_ckd` functions already follow this pattern correctly; `respond_verify_foreign_tx` is the outlier.

---

### Proof of Concept

**Setup:**
- MPC network with `n` participants, threshold `t`. Attacker controls participant `P_evil` (one node, below threshold).
- Two users, Alice and Bob, each submit a `verify_foreign_transaction` request for different Bitcoin transactions: R_alice (tx_id=`[0xAA;32]`) and R_bob (tx_id=`[0xBB;32]`).

**Step 1 — Obtain a valid signature for Alice's request:**
The threshold protocol runs for R_alice. All `t+1` honest nodes (including `P_evil`) participate. The protocol produces `sig_alice` over `hash(R_alice, block_hash_alice)`. `P_evil` saves `sig_alice` and `hash(R_alice, block_hash_alice)`.

**Step 2 — Race to respond to Bob's request:**
Before any honest node calls `respond_verify_foreign_tx` for R_bob, `P_evil` calls:

```
respond_verify_foreign_tx(
  request = R_bob,                              // valid pending key
  response = {
    payload_hash: hash(R_alice, block_hash_alice),  // wrong hash
    signature:    sig_alice,                         // valid sig over wrong hash
  }
)
```

**Step 3 — Contract accepts:**
- `assert_caller_is_attested_participant_and_protocol_active` passes (P_evil is a valid participant). [8](#0-7) 
- `verify_ecdsa_signature(sig_alice, hash(R_alice, block_hash_alice), root_pk)` returns `Ok` — the signature is genuinely valid. [9](#0-8) 
- `resolve_yields_for` drains R_bob's pending queue and delivers `{ payload_hash: hash(R_alice, block_hash_alice), sig_alice }` to Bob. [3](#0-2) 

**Step 4 — Bob's bridge contract is deceived:**
Bob's bridge contract receives a response claiming the MPC network verified and signed a payload encoding `block_hash_alice` for Bob's transaction `[0xBB;32]`. If Bob's contract does not independently recompute the expected hash (i.e., does not use `ForeignChainSignatureVerifier::verify_signature`), it accepts this as proof that Bob's transaction was confirmed in Alice's block — enabling invalid bridge execution.

### Citations

**File:** crates/contract/src/lib.rs (L600-607)
```rust
                let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");

                // Check the signature is correct
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    payload_hash,
                    &expected_public_key,
                )
```

**File:** crates/contract/src/lib.rs (L705-705)
```rust
        self.assert_caller_is_attested_participant_and_protocol_active();
```

**File:** crates/contract/src/lib.rs (L718-734)
```rust
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
```

**File:** crates/contract/src/lib.rs (L749-753)
```rust
        pending_requests::resolve_yields_for(
            &mut self.pending_verify_foreign_tx_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
```

**File:** crates/contract/src/lib.rs (L2389-2403)
```rust
    fn assert_caller_is_attested_participant_and_protocol_active(&self) {
        let participants = self.protocol_state.active_participants();

        Self::assert_caller_is_signer();

        let attestation_check = self
            .tee_state
            .is_caller_an_attested_participant(participants);

        assert_matches::assert_matches!(
            attestation_check,
            Ok(()),
            "Caller must be an attested participant"
        );
    }
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L147-150)
```rust
pub struct VerifyForeignTransactionResponse {
    pub payload_hash: Hash256,
    pub signature: SignatureResponse,
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
