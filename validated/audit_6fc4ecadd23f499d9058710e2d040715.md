### Title
`respond_verify_foreign_tx` Does Not Bind `payload_hash` to the Queued `request` — Cross-Request Payload Hash Substitution by a Byzantine Leader Node - (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_verify_foreign_tx` verifies that `response.signature` is a valid MPC root-key signature over `response.payload_hash`, but it never checks that `response.payload_hash` is actually the canonical hash of a `ForeignTxSignPayload` derived from the `request` argument used as the queue lookup key. A single Byzantine attested leader node can reuse a legitimately-produced root-key signature from one foreign-tx verification round to resolve a completely different pending `verify_foreign_transaction` request, delivering a forged `VerifyForeignTransactionResponse` to the caller.

---

### Finding Description

**Root cause — missing binding check in `respond_verify_foreign_tx`:**

The contract's `respond_verify_foreign_tx` method performs two checks:

1. The caller is an attested participant.
2. `response.signature` is a valid ECDSA signature over `response.payload_hash` under the root MPC public key (no tweak). [1](#0-0) 

It then resolves all yields queued under `request`: [2](#0-1) 

**What is never checked:** that `response.payload_hash` equals `SHA-256(borsh(ForeignTxSignPayload::V1 { request: request.request, values: <any valid values> }))`. The `request` parameter is used only as a map lookup key; the `payload_hash` in the response is completely decoupled from it.

The canonical hash formula is: [3](#0-2) 

The `VerifyForeignTransactionRequest` stored as the queue key contains only `request`, `domain_id`, and `payload_version` — no caller identity and no expected hash: [4](#0-3) 

**Why foreign-tx signatures are reusable across requests:** Unlike regular `sign` requests (which use a per-caller derived key via `tweak`), `respond_verify_foreign_tx` verifies against the bare root public key with no tweak: [5](#0-4) 

This means any valid root-key signature produced during one `verify_foreign_transaction` round is cryptographically valid input for `respond_verify_foreign_tx` on any other pending request.

**Attack path (single Byzantine leader node, below threshold):**

1. Two requests are pending: Request A (`bitcoin_tx_id_X`) and Request B (`bitcoin_tx_id_Y`).
2. The MPC network honestly runs the threshold signing protocol for Request A, producing `sig_A` over `hash_A = SHA-256(borsh(ForeignTxSignPayload::V1{request_A, values_A}))`. The leader node receives `sig_A`.
3. The Byzantine leader calls `respond_verify_foreign_tx(request=A, response={payload_hash=hash_A, sig_A})` — resolves A correctly.
4. The Byzantine leader immediately calls `respond_verify_foreign_tx(request=B, response={payload_hash=hash_A, sig_A})` — the contract accepts this because `sig_A` is a valid root-key signature over `hash_A`, and Request B exists in `pending_verify_foreign_tx_requests`. Request B's callers receive `{payload_hash: hash_A, signature: sig_A}`.

The caller of Request B receives a `VerifyForeignTransactionResponse` bearing a valid MPC signature, but the `payload_hash` corresponds to a completely different foreign transaction (bitcoin_tx_id_X, not bitcoin_tx_id_Y).

The fan-out design (caller-agnostic queue key, confirmed by the test comment "both yields are queued under the single (caller-agnostic) request key") means this forged response is delivered to every caller who submitted Request B: [6](#0-5) 

---

### Impact Explanation

A bridge contract or NEAR smart contract that calls `verify_foreign_transaction` to gate an action (e.g., releasing bridged funds after a foreign-chain deposit) receives a `VerifyForeignTransactionResponse` with a valid MPC signature. If the caller does not independently recompute the expected `payload_hash` from the transaction it submitted and compare it to `response.payload_hash`, it will accept the forged response as proof that its specific foreign transaction was verified. This enables:

- **Invalid bridge execution**: funds released on NEAR for a foreign-chain event that was never verified (or verified for a different transaction).
- **Double-spend**: the same foreign-chain event's signature reused to satisfy multiple independent bridge requests.

This matches the allowed impact: **High — forged foreign-chain verification that causes invalid bridge execution or double-spend conditions.**

---

### Likelihood Explanation

- Requires exactly **one** Byzantine attested MPC node that is elected leader for at least one `verify_foreign_transaction` round — strictly below the signing threshold.
- The leader role is deterministic and rotates; an attacker controlling one node can wait for their turn.
- No threshold collusion is needed: the threshold protocol honestly produces `sig_A`; the Byzantine leader simply reuses it in a second `respond_verify_foreign_tx` call.
- The attack is a single on-chain transaction after the honest signing round completes.
- Caller-side SDK verification (`ForeignChainSignatureVerifier`) is optional and not enforced by the contract.

---

### Recommendation

In `respond_verify_foreign_tx`, require the responder to supply the full `ForeignTxSignPayload` (including extracted values) and enforce on-chain that:

```
response.payload_hash == SHA-256(borsh(ForeignTxSignPayload::V1 { request: request.request, values: <provided values> }))
```

This binds the `payload_hash` to the specific `request` in the queue, making cross-request substitution impossible regardless of which node submits the response.

---

### Proof of Concept

```
// State: Request A (tx_id=X) and Request B (tx_id=Y) both pending.

// Step 1: MPC honestly signs Request A → leader holds (hash_A, sig_A).

// Step 2: Leader resolves A normally.
respond_verify_foreign_tx(
    request = VerifyForeignTransactionRequest { request: bitcoin(tx_id=X), ... },
    response = { payload_hash: hash_A, signature: sig_A }
)
// → contract: sig_A valid over hash_A ✓, request A found ✓ → resolves A.

// Step 3: Leader resolves B with A's signature.
respond_verify_foreign_tx(
    request = VerifyForeignTransactionRequest { request: bitcoin(tx_id=Y), ... },
    response = { payload_hash: hash_A, signature: sig_A }  // ← hash_A ≠ hash(tx_id=Y)
)
// → contract: sig_A valid over hash_A ✓, request B found ✓ → resolves B.
// Callers of B receive { payload_hash: hash_A, sig_A } — forged verification.
```

The contract's signature check (lines 718–734 of `crates/contract/src/lib.rs`) passes because `sig_A` is a genuine root-key signature. The missing binding check means the `request` key and `payload_hash` are never correlated. [7](#0-6)

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

**File:** crates/contract/src/lib.rs (L3255-3263)
```rust
        // Then: both yields are queued under the single (caller-agnostic) request key.
        assert_eq!(
            contract
                .pending_verify_foreign_tx_requests
                .get(&request)
                .map(|q| q.len()),
            Some(2),
            "duplicate foreign-tx requests from different callers should fan out",
        );
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L124-128)
```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L1504-1509)
```rust
impl ForeignTxSignPayload {
    pub fn compute_msg_hash(&self) -> std::io::Result<Hash256> {
        let mut hasher = sha2::Sha256::new();
        borsh::BorshSerialize::serialize(self, &mut hasher)?;
        Ok(Hash256(hasher.finalize().into()))
    }
```
