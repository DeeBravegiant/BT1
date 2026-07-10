### Title
`respond_verify_foreign_tx` Does Not Bind `payload_hash` to the Pending Request — (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_verify_foreign_tx` verifies that the caller-supplied `response.payload_hash` carries a valid root-key signature, but **never checks that `payload_hash` was derived from the pending `request`**. A single Byzantine attested participant can replay any previously observed on-chain signature (from a completed foreign-tx verification) against a different pending request, causing the contract to resolve that request with a forged `payload_hash` and a cryptographically valid-looking signature.

---

### Finding Description

In `respond_verify_foreign_tx` the contract performs three checks:

1. Caller is an attested participant.
2. The ECDSA signature in `response` is valid over `response.payload_hash` against the **root** public key.
3. The `request` key exists in `pending_verify_foreign_tx_requests`. [1](#0-0) 

What is **absent**: any verification that `response.payload_hash` equals `SHA-256(borsh(ForeignTxSignPayload { request, values }))` for the specific `request` being resolved. The contract stores the request in the pending map but never uses it to constrain the hash. [2](#0-1) 

The canonical hash is defined as:

```
msg_hash = SHA-256(borsh(ForeignTxSignPayload { request, values }))
``` [3](#0-2) 

The MPC nodes compute and sign this hash off-chain, then submit it together with the signature via `respond_verify_foreign_tx`. The contract trusts whatever `payload_hash` the caller provides, as long as the signature over it is valid.

Because every `respond_verify_foreign_tx` call is a public on-chain transaction, any observer can extract a `(payload_hash_A, signature_A)` pair from a completed request A. A Byzantine attested participant can then:

1. Let request A complete normally, recording `payload_hash_A` and `signature_A` from the on-chain call.
2. Submit (or wait for) a new `verify_foreign_transaction` request B for a **different** foreign-chain transaction.
3. Call `respond_verify_foreign_tx(request_B, { payload_hash: payload_hash_A, signature: signature_A })`.
4. The contract accepts: signature is valid over `payload_hash_A`, and request B exists in the pending map.
5. Request B is resolved and the caller receives `{ payload_hash_A, signature_A }`. [4](#0-3) 

---

### Impact Explanation

The bridge service (or any caller of `verify_foreign_transaction`) receives a `VerifyForeignTransactionResponse` whose signature is cryptographically valid but whose `payload_hash` encodes the **wrong** foreign-chain state (request A's extracted values, not request B's). Because the extracted values are never returned to the caller — only the hash is — the caller cannot independently reconstruct the expected hash and detect the mismatch. [5](#0-4) 

A bridge contract that gates an inbound transfer on a valid `VerifyForeignTransactionResponse` would accept the forged attestation, enabling **invalid bridge execution or double-spend conditions** (e.g., attesting that a deposit transaction finalized when it did not, or attesting the wrong block hash / log data).

---

### Likelihood Explanation

- The attacker must be a single **attested MPC participant** — below the signing threshold.
- No key material needs to be stolen; the `(payload_hash, signature)` pair is already public on-chain from any prior completed request.
- The attacker does not need to collude with other nodes or break any cryptographic primitive.
- The window of opportunity is any time a pending `verify_foreign_transaction` request exists in the contract.

---

### Recommendation

The contract must bind `payload_hash` to the pending request before accepting the response. Two complementary approaches:

1. **Include extracted values in the response** and have the contract recompute `SHA-256(borsh(ForeignTxSignPayload { request, values }))`, asserting it equals `response.payload_hash`. This is the strongest fix but increases on-chain data size.

2. **Commit to the request inside the hash**: require that the `payload_hash` computation always includes a canonical serialization of the `ForeignChainRpcRequest` (already the case in `ForeignTxSignPayloadV1`), and have the contract verify at least that the hash is consistent with the stored request by checking a request-derived prefix or nonce embedded in the signed payload.

Either way, `respond_verify_foreign_tx` must reject any response whose `payload_hash` is not provably derived from the specific pending request being resolved.

---

### Proof of Concept

```
// Step 1 – observe a completed request on-chain
let payload_hash_A = <from on-chain respond_verify_foreign_tx call for request A>;
let signature_A    = <from same call>;

// Step 2 – submit a new request for a different (possibly non-existent) tx
contract.verify_foreign_transaction({
    domain_id: foreign_tx_domain,
    payload_version: V1,
    request: BitcoinRpcRequest { tx_id: [0xff; 32], confirmations: 1, extractors: [BlockHash] },
});

// Step 3 – Byzantine participant resolves request B with request A's hash+sig
contract.respond_verify_foreign_tx(
    request_B,
    VerifyForeignTransactionResponse {
        payload_hash: payload_hash_A,   // ← wrong hash, not derived from request B
        signature:    signature_A,      // ← valid signature over payload_hash_A
    }
);

// Result: contract accepts; caller of request B receives (payload_hash_A, signature_A)
// Bridge contract verifies signature ✓, but payload_hash encodes tx A's block-hash, not tx B's.
```

### Citations

**File:** crates/contract/src/lib.rs (L544-556)
```rust
        let callback_gas = Gas::from_tgas(
            self.config
                .return_signature_and_clean_state_on_success_call_tera_gas,
        );

        let request = args_into_verify_foreign_tx_request(request);
        let callback_args = serde_json::to_vec(&(&request,)).unwrap();
        self.enqueue_yield_request(
            method_names::RETURN_VERIFY_FOREIGN_TX_AND_CLEAN_STATE_ON_SUCCESS,
            callback_args,
            callback_gas,
            move |this, id| this.add_verify_foreign_tx_request(request, id),
        );
```

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
