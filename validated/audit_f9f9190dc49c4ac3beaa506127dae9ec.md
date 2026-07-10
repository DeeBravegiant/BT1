### Title
`respond_verify_foreign_tx` Does Not Bind `payload_hash` to the Submitted `request`, Enabling Cross-Request Signature Replay - (File: `crates/contract/src/lib.rs`)

---

### Summary

The `respond_verify_foreign_tx` contract method verifies that the submitted signature is valid over `response.payload_hash`, but never verifies that `response.payload_hash` was actually computed from the specific `request` argument. A single Byzantine MPC participant (below threshold) can replay a valid signature obtained from a previous foreign-chain verification session as a response to a completely different pending request, delivering a forged verification result to the caller.

---

### Finding Description

`respond_verify_foreign_tx` in `crates/contract/src/lib.rs` performs three checks:

1. The caller is an attested participant.
2. The signature in `response` is valid over `response.payload_hash` using the root public key.
3. The `request` struct exists as a key in `pending_verify_foreign_tx_requests`. [1](#0-0) 

What it does **not** check is whether `response.payload_hash` was derived from the specific `request` parameter. The `payload_hash` is consumed directly from the caller-supplied response without any re-derivation.

The MPC nodes sign `payload_hash = SHA256(borsh(ForeignTxSignPayloadV1 { request, values }))`, where `request` is the `ForeignChainRpcRequest` and `values` are the extracted on-chain values. [2](#0-1) 

The contract cannot recompute this hash because the extracted `values` are never stored on-chain. This creates a structural gap: the contract can confirm the signature is valid over *some* hash, but cannot confirm that hash corresponds to the specific request being resolved.

Critically, the `SignatureRequest` built for foreign-tx signing uses a **zero tweak**: [3](#0-2) 

This means the signature is over the raw `payload_hash` under the root key with no per-request derivation. Unlike regular `sign()` requests (where the tweak binds the signature to a specific `(predecessor, path)` pair), foreign-tx signatures carry no request-specific context that the contract can verify.

Any attested participant can call `respond_verify_foreign_tx`. A Byzantine participant who has observed a previously completed legitimate response `{payload_hash: H_A, signature: S_A}` for request A can immediately call:

```
respond_verify_foreign_tx(request_B, { payload_hash: H_A, signature: S_A })
```

The contract will:
- Confirm `S_A` is valid over `H_A` ✓
- Find `request_B` in the pending map ✓
- Resolve all queued yields for `request_B` with `{ payload_hash: H_A, signature: S_A }` [4](#0-3) 

The caller of `verify_foreign_transaction(request_B)` receives a response that was computed for a completely different transaction (A).

---

### Impact Explanation

A bridge contract that calls `verify_foreign_transaction(request_B)` to confirm that foreign-chain transaction B is finalized will receive `{ payload_hash: H_A, signature: S_A }`. If the bridge contract verifies only that the signature is valid over `payload_hash` (which it is — `S_A` over `H_A` is a genuine MPC signature) without independently recomputing the expected hash from `request_B`, it will accept the forged verification and execute the bridge action (e.g., release funds, mint tokens) for an unverified or invalid transaction B.

The SDK helper `ForeignChainSignatureVerifier::verify_signature` does perform the binding check: [5](#0-4) 

However, the contract — the trust anchor — does not enforce this check. Bridge contracts that do not use the SDK or that omit the `payload_hash` binding step are silently exposed. The impact is forged foreign-chain verification enabling invalid bridge execution or double-spend conditions.

---

### Likelihood Explanation

- A single Byzantine MPC participant (strictly below threshold) can execute this attack with no cryptographic forgery.
- No new signature needs to be produced; the attacker only replays a previously observed legitimate response.
- All on-chain `respond_verify_foreign_tx` calls are public; any participant can observe `H_A` and `S_A` from a completed request.
- The attacker only needs to be an attested participant and submit the transaction before the target request times out (200 blocks). [6](#0-5) 

---

### Recommendation

Bind the signature to the specific request by including the unique `VerifyForeignTxId` (the `CryptoHash` receipt-derived ID) in the signed payload. For example, nodes should sign:

```
SHA256( request_id || SHA256(borsh(ForeignTxSignPayloadV1 { request, values })) )
```

The contract already has access to the `request` struct (and can derive its ID), so it can verify this binding without needing the extracted values. Alternatively, the contract should recompute a commitment to the `ForeignChainRpcRequest` portion of the payload and verify that `response.payload_hash` is consistent with it.

---

### Proof of Concept

1. Alice submits `verify_foreign_transaction(request_A)` for Bitcoin tx A (6 confirmations, block hash `B_A`).
2. MPC nodes compute `payload_hash_A = SHA256(borsh(ForeignTxSignPayloadV1{request_A, [BlockHash(B_A)]}))` and collectively sign it → `sig_A`.
3. Leader calls `respond_verify_foreign_tx(request_A, { payload_hash: payload_hash_A, sig: sig_A })`. Alice receives the legitimate response.
4. Bob submits `verify_foreign_transaction(request_B)` for Bitcoin tx B (0 confirmations / invalid).
5. Byzantine participant (having observed step 3) calls:
   ```
   respond_verify_foreign_tx(request_B, { payload_hash: payload_hash_A, sig: sig_A })
   ```
6. Contract checks: `sig_A` valid over `payload_hash_A` ✓; `request_B` pending ✓. Resolves Bob's yield with the forged response.
7. Bob's bridge contract receives `{ payload_hash: payload_hash_A, sig: sig_A }`, verifies the ECDSA signature (passes), and — without checking that `payload_hash_A` encodes `request_B` — releases funds for the unconfirmed transaction B. [7](#0-6) [8](#0-7)

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

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L1504-1509)
```rust
impl ForeignTxSignPayload {
    pub fn compute_msg_hash(&self) -> std::io::Result<Hash256> {
        let mut hasher = sha2::Sha256::new();
        borsh::BorshSerialize::serialize(self, &mut hasher)?;
        Ok(Hash256(hasher.finalize().into()))
    }
```

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L30-48)
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

**File:** crates/node/src/requests/queue.rs (L32-33)
```rust
/// This is equal to the yield-resume timeout on the blockchain.
pub const REQUEST_EXPIRATION_BLOCKS: NumBlocks = 200;
```
