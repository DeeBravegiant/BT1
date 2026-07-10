### Title
`respond_verify_foreign_tx` Does Not Bind `payload_hash` to the Pending Request — Cross-Request Replay by a Single Byzantine Participant - (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_verify_foreign_tx` verifies only that the supplied `response.signature` is a valid ECDSA signature over `response.payload_hash` under the domain's root public key. It does **not** verify that `response.payload_hash` is the canonical hash of `ForeignTxSignPayload{request: <the pending request>, values: ...}`. A single attested MPC participant (strictly below the signing threshold) can replay any previously observed `(payload_hash, signature)` pair from a prior legitimate response to satisfy a completely different pending request, causing the contract to attest to the wrong foreign-chain observation.

---

### Finding Description

The `respond_verify_foreign_tx` function in `crates/contract/src/lib.rs` performs the following checks:

1. Caller is an attested participant.
2. The `request` key exists in `pending_verify_foreign_tx_requests`.
3. `response.signature` is a valid ECDSA signature over `response.payload_hash` against the domain's root public key. [1](#0-0) 

What it does **not** check is that `response.payload_hash` equals `SHA-256(borsh(ForeignTxSignPayload { request: <the actual pending request>, values: <any values> }))`. The `payload_hash` is entirely caller-supplied and is only verified for signature validity, not for binding to the request being resolved.

The `VerifyForeignTransactionRequest` key stored in `pending_verify_foreign_tx_requests` contains only `domain_id`, `request` (the chain-specific query), and `payload_version` — no `payload_hash`. [2](#0-1) 

On the node side, `build_signature_request` uses a zero tweak (`Tweak::new([0u8; 32])`), meaning all foreign-tx signatures are produced under the **same root key** regardless of which transaction is being verified. [3](#0-2) 

This means every valid `(payload_hash, signature)` pair ever produced for any `verify_foreign_transaction` response is a valid signature under the same root key, and any of them can be replayed to satisfy any other pending request.

---

### Impact Explanation

A single Byzantine attested participant executes the following replay:

1. Observe a past legitimate response for request `tx_A`: `{payload_hash: H_A, signature: sig_A}` (this is public on-chain).
2. Wait for a new `verify_foreign_transaction({tx_id: B, ...})` to enter the pending queue.
3. Call `respond_verify_foreign_tx({tx_id: B, ...}, {payload_hash: H_A, signature: sig_A})`.

The contract checks:
- Is `{tx_id: B, ...}` in `pending_verify_foreign_tx_requests`? **Yes** ✓
- Is `sig_A` a valid signature over `H_A` under the root key? **Yes** ✓
- Is `H_A` the hash of `ForeignTxSignPayload{request: {tx_id: B, ...}, values: ...}`? **Not checked** ✗

The contract resolves the yield for `{tx_id: B}` with `{payload_hash: H_A, sig_A}`. The caller receives an attestation claiming transaction B was verified, but the signed payload actually corresponds to transaction A's observation. Any bridge contract that trusts the MPC attestation (e.g., the Omnibridge inbound flow) would process a forged cross-chain event, enabling double-spend or invalid bridge execution. [4](#0-3) 

---

### Likelihood Explanation

The attacker needs only to be a single attested MPC participant — strictly below the signing threshold. No threshold collusion is required. The attack requires only:
- Access to any previously emitted `VerifyForeignTransactionResponse` (public on-chain data).
- The ability to call `respond_verify_foreign_tx` as an attested participant.

The window is open for every pending `verify_foreign_transaction` request. The attack is deterministic and requires no timing luck.

---

### Recommendation

Before resolving the yield, recompute the expected `payload_hash` from the pending request and verify it matches `response.payload_hash`. Since the contract does not store the extracted `values` (they are determined off-chain), the minimum fix is to verify that `response.payload_hash` is a valid hash of `ForeignTxSignPayload { request: <the pending request>, values: <any values> }` — i.e., that the first `borsh`-serialized field of the preimage matches the stored request.

A simpler and more robust fix: include the `VerifyForeignTransactionRequest` (or its hash) as a domain-separation prefix in the signed payload, and verify on-chain that the prefix matches the pending request. This binds each signature to exactly one request, making cross-request replay cryptographically impossible.

---

### Proof of Concept

```rust
// Step 1: Legitimate response for tx_A is observed on-chain:
//   payload_hash = H_A, signature = sig_A (valid under root key)

// Step 2: Attacker (single attested participant) submits tx_B request
contract.verify_foreign_transaction(VerifyForeignTransactionRequestArgs {
    domain_id: D,
    payload_version: ForeignTxPayloadVersion::V1,
    request: ForeignChainRpcRequest::Bitcoin(BitcoinRpcRequest {
        tx_id: [0xBB; 32].into(), // tx_B
        confirmations: 1.into(),
        extractors: vec![BitcoinExtractor::BlockHash],
    }),
});

// Step 3: Attacker replays tx_A's response for tx_B's pending request
contract.respond_verify_foreign_tx(
    VerifyForeignTransactionRequest {
        domain_id: D,
        payload_version: ForeignTxPayloadVersion::V1,
        request: ForeignChainRpcRequest::Bitcoin(BitcoinRpcRequest {
            tx_id: [0xBB; 32].into(), // tx_B — matches the pending request key
            confirmations: 1.into(),
            extractors: vec![BitcoinExtractor::BlockHash],
        }),
    },
    VerifyForeignTransactionResponse {
        payload_hash: H_A,   // hash of tx_A's payload — NOT tx_B's
        signature: sig_A,    // valid sig over H_A under root key — passes verification
    },
);
// Contract accepts: sig_A is valid for H_A, and tx_B request exists.
// Caller receives forged attestation: payload_hash = H_A (tx_A's data).
``` [5](#0-4) [3](#0-2)

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

**File:** crates/contract/src/dto_mapping.rs (L840-848)
```rust
pub fn args_into_verify_foreign_tx_request(
    args: dtos::VerifyForeignTransactionRequestArgs,
) -> dtos::VerifyForeignTransactionRequest {
    dtos::VerifyForeignTransactionRequest {
        domain_id: args.domain_id,
        request: args.request,
        payload_version: args.payload_version,
    }
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
