### Title
`respond_verify_foreign_tx` Does Not Validate `response.payload_hash` Against the Original Request, Enabling Cross-Request Replay by a Single Malicious Participant — (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_verify_foreign_tx` verifies that the submitted signature is cryptographically valid over the caller-supplied `payload_hash`, but never checks that `payload_hash` is actually derived from the original pending `request`. A single malicious attested MPC participant can take a legitimately produced threshold-signed response for foreign-chain request A and submit it as the resolution for a different pending request B, delivering a forged verification result to the caller of B.

---

### Finding Description

In `respond_verify_foreign_tx` the contract performs two operations:

**Step 1 — signature validity check (lines 718–734):** [1](#0-0) 

The contract verifies that `response.signature` is a valid ECDSA signature over `response.payload_hash` under the root public key. It does **not** verify that `response.payload_hash` is the hash of `ForeignTxSignPayload{request, extracted_values}` for the specific `request` being resolved.

**Step 2 — yield resolution (lines 749–753):** [2](#0-1) 

The contract resolves the pending yield keyed by `request` and delivers the full `response` (including the unvalidated `payload_hash`) to the caller.

The `payload_hash` is defined as `SHA-256(borsh(ForeignTxSignPayload{request, extracted_values}))` per the protocol documentation: [3](#0-2) 

The contract cannot independently recompute this hash because `extracted_values` (the on-chain data observed by MPC nodes from the foreign chain) are not stored on-chain. However, the contract also performs **no partial consistency check** — it does not verify that `payload_hash` encodes the same `request` fields (chain, `tx_id`, `domain_id`, `payload_version`) that are already known from the pending request entry.

**Attack path:**

1. Attacker is an attested MPC participant (satisfies `assert_caller_is_attested_participant_and_protocol_active`). [4](#0-3) 

2. The MPC network legitimately completes signing for request A, producing `response_A = {payload_hash_A, signature_A}`. The attacker, as a participant, observes this response.

3. A separate request B is pending in `pending_verify_foreign_tx_requests`.

4. The attacker calls `respond_verify_foreign_tx(request_B, response_A)`. The contract:
   - Verifies `signature_A` over `payload_hash_A` → **passes** (it is a genuine threshold signature).
   - Resolves the yield for `request_B` with `response_A`.

5. The caller of request B receives `{payload_hash_A, signature_A}` — a response that commits to transaction A's data, not transaction B's.

The node-side code confirms that `payload_hash` is computed from the actual request and extracted values before being placed in the response: [5](#0-4) 

The contract never re-derives or cross-checks this hash against the stored request.

---

### Impact Explanation

**High — Forged foreign-chain verification enabling invalid bridge execution.**

The `verify_foreign_transaction` / `respond_verify_foreign_tx` flow is the protocol's mechanism for attesting that a specific foreign-chain transaction (identified by `tx_id`, chain, extractors) occurred and for delivering a threshold-signed proof of that fact to on-chain callers. A bridge or application contract consuming the response receives `payload_hash` and `signature` and is expected to use them to authorize downstream actions (e.g., releasing funds, minting tokens).

By replaying a valid response for transaction A as the answer to request B, a single malicious participant causes the contract to deliver a signature that commits to the wrong transaction. Any downstream contract that trusts the MPC contract's delivery — rather than independently recomputing the expected `payload_hash` from the original request — will accept a forged attestation. This directly enables invalid bridge execution or double-spend conditions: the attacker can make the bridge believe transaction B was verified when only transaction A was.

---

### Likelihood Explanation

**Medium.** The attacker must be a single attested MPC participant — a realistic threat model for a Byzantine-below-threshold participant. No threshold collusion is required. The attacker only needs:

1. To be an attested participant (satisfies the access check at line 705).
2. Access to a legitimately produced response for any prior or concurrent request (available to all participants who took part in that signing round).
3. A different pending request to target.

Both conditions are routinely satisfied in normal protocol operation. The attack is silent — the contract emits no error, and the caller receives a structurally valid `VerifyForeignTransactionResponse`.

---

### Recommendation

The contract should enforce that `response.payload_hash` is consistent with the fields of `request` that are known on-chain. Concretely:

1. **Commit the request into the signed payload at the protocol level.** The `ForeignTxSignPayload` already includes `request: ForeignChainRpcRequest`. The contract should verify that the first 32 bytes (or a tagged prefix) of the borsh-serialized payload match the stored request. Alternatively, store a `SHA-256(borsh(request))` alongside the pending yield and require that `payload_hash` encodes a payload whose `request` field hashes to the same value.

2. **Include a binding nonce or request-ID in the signed payload.** Add the `VerifyForeignTransactionRequest` hash (or a unique request ID) to `ForeignTxSignPayloadV1` so that a response signed for request A is cryptographically bound to A and cannot be replayed for B.

3. **Validate on the contract side before resolving.** Even without full recomputation, the contract can reject responses where `payload_hash` is identical to one already used to resolve a different request key, limiting replay to fresh signatures.

---

### Proof of Concept

```
// Setup: two pending foreign-tx requests for different tx_ids
// request_A: Bitcoin tx_id = [0xAA; 32]
// request_B: Bitcoin tx_id = [0xBB; 32]

// Step 1: MPC network legitimately signs for request_A
// Attacker (attested participant) observes response_A = {payload_hash_A, signature_A}
// where payload_hash_A = SHA-256(borsh(ForeignTxSignPayload{request_A, [BlockHash([0x11;32])]}))

// Step 2: Attacker calls respond_verify_foreign_tx with request_B but response_A
contract.respond_verify_foreign_tx(request_B, response_A);

// Contract checks:
// 1. verify_ecdsa_signature(signature_A, payload_hash_A, root_pk) → OK (genuine sig)
// 2. resolve_yields_for(request_B, serialize(response_A)) → delivers response_A to caller of B

// Caller of request_B receives:
// { payload_hash: payload_hash_A,   // ← commits to tx_id=[0xAA;32], NOT [0xBB;32]
//   signature: signature_A }        // ← valid threshold sig, but over wrong tx
// Bridge contract sees a valid signature and may authorize release for tx_B
// even though only tx_A was ever verified by the MPC network.
```

The test at `lib.rs:3660` demonstrates the happy path and confirms the contract accepts any `payload_hash` that carries a valid signature, with no check against the stored request: [6](#0-5)

### Citations

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

**File:** crates/contract/src/lib.rs (L3687-3712)
```rust
        let payload = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
            request: request.request.clone(),
            values: vec![ExtractedValue::BitcoinExtractedValue(
                BitcoinExtractedValue::BlockHash([42u8; 32].into()),
            )],
        });
        let payload_hash = payload.compute_msg_hash().unwrap().0;
        // simulate signature with the root key (no tweak for foreign tx)
        let secret_key_ec: elliptic_curve::SecretKey<Secp256k1> =
            elliptic_curve::SecretKey::from_bytes(&secret_key.to_bytes()).unwrap();
        let secret_key = SigningKey::from_bytes(&secret_key_ec.to_bytes()).unwrap();
        let (signature, recovery_id) = secret_key.sign_prehash_recoverable(&payload_hash).unwrap();
        let signature = dtos::SignatureResponse::Secp256k1(
            dtos::K256Signature::from_ecdsa_recoverable(&signature, recovery_id),
        );

        let payload_hash = payload.compute_msg_hash().unwrap();
        let response = VerifyForeignTransactionResponse {
            payload_hash,
            signature,
        };

        with_active_participant_and_attested_context(&contract);

        // Then
        match contract.respond_verify_foreign_tx(request.clone(), response.clone()) {
```

**File:** docs/foreign-chain-transactions.md (L182-189)
```markdown
The 32-byte `msg_hash` that nodes sign is computed as:

```
msg_hash = SHA-256(borsh(ForeignTxSignPayload))
```

Callers select the payload version via `VerifyForeignTransactionRequestArgs::payload_version`.
Borsh field ordering is stability-critical — fields and enum variants must never be reordered.
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
