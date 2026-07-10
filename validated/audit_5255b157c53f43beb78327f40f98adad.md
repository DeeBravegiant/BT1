### Title
`respond_verify_foreign_tx` Does Not Validate `payload_hash` Against the Original Request, Enabling Cross-Request Signature Replay - (File: `crates/contract/src/lib.rs`)

---

### Summary

The `respond_verify_foreign_tx` function in the MPC contract verifies that the submitted signature is cryptographically valid for the `payload_hash` provided in the response, but **never checks that `payload_hash` was actually derived from the original pending request**. A single Byzantine attested participant (strictly below the signing threshold) can observe a legitimately produced signature for one pending foreign-chain verification request and replay it as the response to a *different* pending request, causing the contract to resolve that second request with a forged `payload_hash`. Downstream bridge contracts that trust the on-chain response without independently recomputing the expected hash will execute based on fabricated foreign-chain data.

---

### Finding Description

In `respond_verify_foreign_tx` the contract performs the following checks:

1. Caller is an attested participant (`assert_caller_is_attested_participant_and_protocol_active`).
2. Protocol is running or resharing.
3. The ECDSA signature in `response.signature` is valid for `response.payload_hash` under the domain's **root** public key.
4. A pending yield exists for the supplied `request` key. [1](#0-0) 

What is **absent** is any derivation or comparison of an *expected* `payload_hash` from the original request. The `request` parameter (which carries the original `ForeignChainRpcRequest` and `payload_version`) is used only to look up the pending yield — it is never used to recompute or bound-check the hash that the response claims was signed.

The canonical hash the MPC nodes are supposed to sign is:

```
msg_hash = SHA-256(borsh(ForeignTxSignPayload::V1 { request, values }))
``` [2](#0-1) 

The contract never reconstructs this hash from the stored request, so any 32-byte value that carries a valid root-key signature is accepted as `payload_hash`.

The SDK helper `ForeignChainSignatureVerifier::verify_signature` *does* perform this check client-side: [3](#0-2) 

But that check lives in an off-chain/caller-side library, not in the on-chain contract. The contract itself is the authoritative gate and it is missing the validation.

This is structurally identical to the reference bug: just as `_fetchTwapPrice(Action.None)` silently passes through an invalid price because the `Action.None` branch is never handled, `respond_verify_foreign_tx` silently passes through an unvalidated `payload_hash` because the branch that would compare it against the original request is simply absent.

---

### Impact Explanation

A Byzantine attested participant (one node, below threshold) can:

1. Observe a legitimately completed `respond_verify_foreign_tx` call for **Request A** on-chain — the signature `sig_A` and `payload_hash_A` are now public.
2. Call `respond_verify_foreign_tx(request_B, { payload_hash: payload_hash_A, signature: sig_A })` for a *different* pending **Request B** (e.g., a different chain, different transaction ID, or different extracted values).
3. The contract accepts the call: `sig_A` is a valid root-key signature over `payload_hash_A`, and Request B has a pending yield.
4. The yield for Request B is resolved with the forged `{ payload_hash_A, sig_A }`.
5. Every caller who submitted Request B receives a response whose `payload_hash` corresponds to Request A's foreign-chain data, not Request B's.

Bridge contracts that consume the response without independently recomputing the expected hash will execute based on fabricated foreign-chain state — enabling invalid bridge execution or double-spend conditions.

This matches the allowed High impact: **"Cross-chain replay, forged foreign-chain verification … that causes invalid bridge execution or double-spend conditions."**

---

### Likelihood Explanation

- **Attacker role**: A single attested MPC participant (Byzantine, below threshold). No threshold collusion, no TEE compromise, no privileged operator access is required.
- **Signature availability**: Once any honest node submits a valid `respond_verify_foreign_tx` for Request A, the signature is permanently public on-chain. The attacker does not need to participate in the signing round for Request A.
- **Timing**: The attacker must submit the forged response for Request B before honest nodes do. Given that the attacker can monitor the chain and submit immediately, while honest nodes must first complete foreign-chain RPC inspection, this race is practically winnable.
- **Target availability**: Any pending `verify_foreign_transaction` request is a valid target. In a live bridge environment, multiple requests are pending simultaneously.

---

### Recommendation

**Short term**: In `respond_verify_foreign_tx`, recompute the minimum expected prefix of the `payload_hash` from the stored request. At minimum, verify that the hash encodes the same `ForeignChainRpcRequest` as the pending request (i.e., the first Borsh field of `ForeignTxSignPayloadV1`). If the response's `payload_hash` cannot be shown to commit to the original request, reject it.

**Long term**: Include the extracted values in the response so the contract can fully reconstruct and verify `ForeignTxSignPayload::V1 { request, values }` and compare its SHA-256 against `response.payload_hash` before resolving any yield. This closes the gap between the on-chain gate and the off-chain SDK check that already performs this validation. [4](#0-3) 

---

### Proof of Concept

```
1. Alice submits verify_foreign_transaction(
       request_A = { chain: Bitcoin, tx_id: [0x01;32], extractors: [BlockHash] }
   )  →  pending yield Y_A

2. Bob submits verify_foreign_transaction(
       request_B = { chain: Ethereum, tx_id: [0x02;32], extractors: [BlockHash] }
   )  →  pending yield Y_B

3. Honest nodes process request_A, produce:
       payload_hash_A = SHA256(borsh(V1 { request_A, [bitcoin_block_hash] }))
       sig_A          = ECDSA_root_key(payload_hash_A)
   Honest node calls respond_verify_foreign_tx(request_A, { payload_hash_A, sig_A })
   → Y_A resolved correctly.

4. Malicious attested participant observes (payload_hash_A, sig_A) on-chain.

5. Malicious node calls:
       respond_verify_foreign_tx(request_B, { payload_hash: payload_hash_A, sig: sig_A })

6. Contract checks:
   - caller is attested participant  ✓
   - sig_A valid for payload_hash_A under root key  ✓
   - request_B has pending yield Y_B  ✓
   - payload_hash_A derived from request_B?  ← NOT CHECKED

7. Y_B is resolved with { payload_hash_A, sig_A }.

8. Bob's bridge contract receives payload_hash_A (Bitcoin block hash for tx [0x01;32])
   and treats it as proof that Ethereum tx [0x02;32] finalized — forged verification.
``` [5](#0-4) [1](#0-0)

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

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L1453-1460)
```rust
/// Canonical payload for foreign-chain transaction verification signatures.
///
/// This enum is Borsh-serialized and SHA-256 hashed to produce the 32-byte
/// `msg_hash` that the MPC network signs. Callers select the payload version
/// via `VerifyForeignTransactionRequestArgs::payload_version`.
///
/// IMPORTANT: Never reorder existing enum variants or struct fields, as this
/// would change the Borsh encoding and break signature verification.
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
