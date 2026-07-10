### Title
`respond_verify_foreign_tx` Accepts Any Valid MPC Signature as Proof for Any Pending Request Without Binding `payload_hash` to the Resolved Request — (File: `crates/contract/src/lib.rs`)

---

### Summary

The `respond_verify_foreign_tx` function verifies that `response.signature` is a valid MPC threshold signature over `response.payload_hash`, but never checks that `response.payload_hash` is the hash of `ForeignTxSignPayload::V1 { request: request.request, values: ... }` for the specific `request` being resolved. A single Byzantine attested node (strictly below the signing threshold) can front-run the legitimate response for any pending foreign-tx request by replaying a valid MPC signature obtained from a prior legitimate signing, delivering a forged attestation to the waiting caller.

---

### Finding Description

The `respond_verify_foreign_tx` entry point at `crates/contract/src/lib.rs:692–754` accepts two independent arguments:

- `request: VerifyForeignTransactionRequest` — used only as a map key to locate the pending yield queue.
- `response: VerifyForeignTransactionResponse { payload_hash, signature }` — the attestation delivered to the caller.

The contract's only cryptographic check is:

```rust
let payload_hash: [u8; 32] = response.payload_hash.0;
// Check the signature is correct against the root public key
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,
)
.is_ok()
``` [1](#0-0) 

This confirms only that `signature` is a valid ECDSA signature over `payload_hash` under the MPC root key. It does **not** verify that `payload_hash == SHA-256(borsh(ForeignTxSignPayload::V1 { request: request.request, values: … }))` for any set of values consistent with the resolved request.

By contrast, the regular `respond` function derives `payload_hash` directly from the stored `request.payload` (the exact bytes the user submitted), so no mismatch is possible there:

```rust
let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    payload_hash,
    &expected_public_key,
)
``` [2](#0-1) 

For `verify_foreign_transaction`, the payload is not fixed at submission time — it is determined by what the MPC nodes observe on the foreign chain. The `ForeignTxSignPayloadV1` struct carries `(request, values)` and its hash is computed by the nodes:

```rust
pub struct ForeignTxSignPayloadV1 {
    pub request: ForeignChainRpcRequest,
    pub values: Vec<ExtractedValue>,
}
impl ForeignTxSignPayload {
    pub fn compute_msg_hash(&self) -> std::io::Result<Hash256> { … }
}
``` [3](#0-2) 

Because the contract never reconstructs or checks this hash against the `request` key, any 32-byte value accompanied by a valid root-key signature is accepted.

**Attack path (single Byzantine attested node, no threshold collusion):**

1. Attacker is one attested MPC participant (below signing threshold).
2. Attacker observes a legitimate `respond_verify_foreign_tx` call for `request_A` (e.g., Bitcoin `tx_id_A`, 1 BTC deposit), capturing `(payload_hash_A, signature_A)`.
3. Victim submits `verify_foreign_transaction` for `request_B` (e.g., Bitcoin `tx_id_B`, 100 BTC deposit).
4. Before the honest node submits the legitimate response for `request_B`, the attacker calls `respond_verify_foreign_tx(request_B, response_A)` where `response_A = { payload_hash_A, signature_A }`.
5. The contract verifies: `signature_A` is valid over `payload_hash_A` under the root key ✓. It resolves `request_B`'s pending yield with `response_A`.
6. The victim's NEAR contract receives `VerifyForeignTransactionResponse { payload_hash: payload_hash_A, signature: signature_A }` — an attestation for `tx_id_A`, not `tx_id_B`.

The `VerifyForeignTransactionResponse` carries no nonce, timestamp, or binding to the originating request: [4](#0-3) 

The `ForeignTxSignPayload` similarly carries no replay-prevention field: [5](#0-4) 

The SDK's `ForeignChainSignatureVerifier::verify_signature` would catch the mismatch if the caller uses it: [6](#0-5) 

However, the contract itself imposes no such check, so any bridge contract that trusts the on-chain response without re-verifying `payload_hash` is exposed.

---

### Impact Explanation

The primary use case of `verify_foreign_transaction` is the Omnibridge inbound flow: a NEAR bridge contract calls this to obtain a signed attestation that a foreign-chain deposit occurred, then mints wrapped tokens. If the attacker delivers an attestation for a 1 BTC deposit in response to a 100 BTC deposit request, a bridge contract that does not re-verify `payload_hash` against its expected values will mint tokens for the wrong amount. This constitutes **forged foreign-chain verification enabling invalid bridge execution and potential double-spend conditions**, matching the High allowed impact tier.

---

### Likelihood Explanation

The attacker requires only:
- Membership as a single attested MPC participant (below signing threshold) — no threshold collusion needed.
- Observation of any prior legitimate `respond_verify_foreign_tx` call to capture a reusable `(payload_hash, signature)` pair.
- The ability to submit a NEAR transaction before the honest node's response lands (standard front-running within a block).

All three conditions are realistic in a live network. The `respond_verify_foreign_tx` function is callable by any single attested node: [7](#0-6) 

---

### Recommendation

In `respond_verify_foreign_tx`, require the responding node to also submit the extracted `values` alongside the response, then verify on-chain that:

```
response.payload_hash == SHA-256(borsh(ForeignTxSignPayload::V1 {
    request: request.request,
    values: submitted_values,
}))
```

This binds the `payload_hash` to the specific `request` being resolved, preventing cross-request replay. It mirrors the fix implied by the external report — adding a `minAmountOut` parameter so the output is validated against the user's intent — here expressed as: the contract must verify the signed hash is structurally consistent with the request it is resolving.

---

### Proof of Concept

```
// Step 1: Legitimate flow for request_A
user_A.verify_foreign_transaction({ tx_id: tx_id_A, extractors: [BlockHash], ... })
// MPC signs payload_hash_A = SHA256(borsh(request_A, {BlockHash: block_A}))
// Honest node calls: respond_verify_foreign_tx(request_A, { payload_hash_A, sig_A })
// user_A receives correct attestation.

// Step 2: Victim submits request

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

**File:** crates/contract/src/lib.rs (L692-705)
```rust
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
```

**File:** crates/contract/src/lib.rs (L726-734)
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

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L1482-1509)
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
