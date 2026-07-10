### Title
Caller-Supplied `payload_hash` in `respond_verify_foreign_tx` Enables Cross-Request Response Replay - (`crates/contract/src/lib.rs`)

### Summary

`respond_verify_foreign_tx` verifies that the submitted signature is valid for the caller-supplied `response.payload_hash`, but never checks that `payload_hash` is the correct hash for the `request` being resolved. A single Byzantine attested participant can replay a valid `(payload_hash, signature)` pair from any prior on-chain response to resolve a completely different pending `verify_foreign_transaction` request, causing the bridge caller to receive a cryptographically valid attestation that corresponds to a different foreign-chain transaction.

### Finding Description

In `respond_verify_foreign_tx`, the contract performs the following signature check:

```rust
let payload_hash: [u8; 32] = response.payload_hash.0;   // ← caller-supplied

near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,
)
.is_ok()
``` [1](#0-0) 

The contract verifies only that `signature` is a valid ECDSA signature over `payload_hash` under the root MPC key. It does **not** verify that `payload_hash` is the canonical hash of `ForeignTxSignPayload{request, extracted_values}` for the specific `request` being resolved.

Compare this to the regular `respond` path, where the payload hash is taken directly from the stored request (not from the caller):

```rust
let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");
``` [2](#0-1) 

The `ForeignTxSignPayload` that nodes sign encodes both the request and the extracted values:

```rust
pub struct ForeignTxSignPayloadV1 {
    pub request: ForeignChainRpcRequest,
    pub values: Vec<ExtractedValue>,
}
``` [3](#0-2) 

Because the contract stores only the `request` key in `pending_verify_foreign_tx_requests` (not the extracted values or the expected hash), it has no way to recompute the expected `payload_hash` at respond time. The contract therefore cannot bind the response hash to the request. [4](#0-3) 

### Impact Explanation

A Byzantine attested participant (strictly below the signing threshold) can:

1. Observe any previously successful `respond_verify_foreign_tx(request_A, {payload_hash_A, sig_A})` call on-chain.
2. Wait for a new `verify_foreign_transaction(request_B)` to be submitted (e.g., for a non-existent or unfinalized foreign transaction).
3. Call `respond_verify_foreign_tx(request_B, {payload_hash_A, sig_A})`.
4. The contract accepts: `sig_A` is a valid MPC signature over `payload_hash_A`, and `request_B` exists in `pending_verify_foreign_tx_requests`.
5. `request_B` is resolved and the bridge caller receives `{payload_hash_A, sig_A}` — a cryptographically valid attestation that actually corresponds to `request_A`'s transaction, not `request_B`'s.

The `VerifyForeignTransactionResponse` returned to the caller contains only `payload_hash` and `signature`, not the extracted values: [5](#0-4) 

Because the extracted values are not returned, the bridge caller cannot independently recompute the expected hash to detect the mismatch. A bridge contract that trusts the MPC response and uses the returned `payload_hash` to verify the signature will find the signature valid (it is), but the hash attests to a different foreign-chain transaction. This enables **forged foreign-chain verification** and **invalid bridge execution**.

### Likelihood Explanation

- A single Byzantine attested participant (below threshold) can call `respond_verify_foreign_tx` unilaterally — no threshold cooperation is required.
- Valid `(payload_hash, signature)` pairs are permanently visible on-chain from prior successful responses.
- Any pending `verify_foreign_transaction` request is a valid target.
- The attack is profitable: the attacker can cause a bridge to process a fraudulent inbound transfer by making an unfinalized or non-existent foreign transaction appear verified.

### Recommendation

The contract must bind the response hash to the request at respond time. Two complementary fixes:

1. **Store the expected payload hash at request submission time** (if the hash can be computed without extracted values — it cannot for V1, since extracted values are off-chain). Alternatively, have nodes include the extracted values in the response so the contract can recompute and verify the hash.

2. **Include the `ForeignChainRpcRequest` in the signed payload hash verification**: at minimum, the contract should verify that the `payload_hash` decodes to a `ForeignTxSignPayload` whose `.request` field matches the `request` parameter. This requires either storing the expected hash or having nodes include the extracted values in the response so the contract can recompute it.

The simplest safe fix is to have `respond_verify_foreign_tx` accept the extracted values alongside the signature, recompute `SHA-256(borsh(ForeignTxSignPayload{request, values}))` on-chain, and verify the signature against that recomputed hash rather than the caller-supplied `payload_hash`.

### Proof of Concept

```
1. Alice submits verify_foreign_transaction(request_A = Bitcoin tx_id=0xAA).
2. MPC nodes compute payload_hash_A = SHA-256(borsh({request_A, [BlockHash(0x...)]})),
   sign it, and call respond_verify_foreign_tx(request_A, {payload_hash_A, sig_A}).
   → This is recorded on-chain.

3. Bob submits verify_foreign_transaction(request_B = Bitcoin tx_id=0xBB)
   where tx_id=0xBB does not exist on Bitcoin.

4. Byzantine participant Eve (single node, below threshold) calls:
   respond_verify_foreign_tx(request_B, {payload_hash_A, sig_A})

5. Contract checks:
   a. Eve is an attested participant ✓
   b. verify_ecdsa_signature(sig_A, payload_hash_A, root_pk) → valid ✓
   c. request_B exists in pending_verify_foreign_tx_requests ✓
   → Contract resolves request_B with {payload_hash_A, sig_A}.

6. Bob's bridge contract receives {payload_hash_A, sig_A}.
   It verifies: verify_ecdsa_signature(sig_A, payload_hash_A, root_pk) → valid ✓
   It cannot detect that payload_hash_A encodes request_A, not request_B.
   → Bridge processes a fraudulent inbound transfer for a non-existent transaction.
```

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

**File:** crates/contract/src/pending_requests.rs (L66-88)
```rust
pub(crate) fn resolve_yields_for<K>(
    requests: &mut LookupMap<K, Vec<YieldIndex>>,
    request: &K,
    response_bytes: Vec<u8>,
) -> Result<(), Error>
where
    K: BorshSerialize + BorshDeserialize + Clone + Ord,
{
    let resumed = requests
        .remove(request)
        .unwrap_or_default()
        .into_iter()
        .map(|YieldIndex { data_id }| {
            env::promise_yield_resume(&data_id, response_bytes.clone());
        })
        .count();

    if resumed > 0 {
        Ok(())
    } else {
        Err(InvalidParameters::RequestNotFound.into())
    }
}
```
