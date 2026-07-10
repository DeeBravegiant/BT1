### Title
Forged Foreign-Chain Verification Response via Cross-Request Payload Hash Replay in `respond_verify_foreign_tx` - (File: crates/contract/src/lib.rs)

### Summary

`respond_verify_foreign_tx` verifies that the submitted signature is cryptographically valid over `response.payload_hash`, but never verifies that `response.payload_hash` was actually derived from the `request` argument being resolved. A single malicious MPC participant (below signing threshold) can reuse a legitimately produced `(payload_hash, signature)` pair from one foreign-chain verification and deliver it as the response to a completely different pending `verify_foreign_transaction` request, causing all callers waiting on that request to receive a forged verification outcome.

### Finding Description

In `respond_verify_foreign_tx`, the signature check is:

```rust
let payload_hash: [u8; 32] = response.payload_hash.0;   // caller-supplied
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,   // verified against THIS hash
    &secp_pk,        // root key, no tweak
)
``` [1](#0-0) 

The contract only checks that `signature` is a valid ECDSA signature over `response.payload_hash` using the root MPC key. It does **not** check that `response.payload_hash` was computed from the `request` argument (i.e., that `payload_hash = H(request, extracted_values)` for the specific `request` being resolved).

Compare this to the regular `respond()` function, where the payload hash is taken from the **on-chain request** (not from the response):

```rust
let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    payload_hash,   // from the stored request, not the response
    &expected_public_key,
)
``` [2](#0-1) 

The `ForeignTxSignPayload` encodes both the request and the extracted values:

```rust
ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
    request: request.clone(),
    values: extracted_values,
})
``` [3](#0-2) 

So `payload_hash_X = H(R_original, extracted_values_original)`. The contract never checks that the `request` embedded in `payload_hash_X` matches the `request` argument passed to `respond_verify_foreign_tx`.

The `VerifyForeignTransactionRequest` key used for the pending-request map contains no caller-specific binding (no tweak, no predecessor account):

```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
``` [4](#0-3) 

This is in contrast to `SignatureRequest`, which embeds a caller-specific `tweak` derived from `(predecessor_id, path)`: [5](#0-4) 

### Impact Explanation

A malicious MPC participant who observes a legitimately produced `(payload_hash_X, signature_X)` for request `R_original` (either from the MPC protocol messages or from the on-chain transaction) can call:

```
respond_verify_foreign_tx(request = R_new, response = {payload_hash_X, signature_X})
```

The contract accepts this because `signature_X` is a valid signature over `payload_hash_X` using the root key. `resolve_yields_for` then drains all yields queued under `R_new`, delivering `{payload_hash_X, signature_X}` to every caller waiting on `R_new`. [6](#0-5) 

Every caller waiting on `R_new` receives a `VerifyForeignTransactionResponse` whose `payload_hash` encodes `R_original`'s transaction data and extracted values — not `R_new`'s. A bridge contract that does not independently recompute and verify the `payload_hash` against its expected transaction will act on a forged attestation, enabling invalid fund release or double-spend.

### Likelihood Explanation

The attack requires a single attested MPC participant (below signing threshold). No threshold collusion is needed to forge the signature — the attacker simply reuses a legitimately produced signature from any prior or concurrent `verify_foreign_transaction` operation. The attacker does not need to front-run: they can observe a past on-chain `respond_verify_foreign_tx` call, extract `(payload_hash, signature)` from it, and replay it against any future pending request with the same `domain_id`. The only precondition is that a pending `R_new` exists in `pending_verify_foreign_tx_requests` at the time of the attack.

### Recommendation

The contract must bind `response.payload_hash` to the `request` being resolved. Since the contract cannot independently recompute the extracted values, the binding should be enforced by requiring that the `ForeignChainRpcRequest` embedded in the signed payload matches the `request` argument. One approach: require the MPC nodes to also submit the `extracted_values` alongside the response, and have the contract recompute `expected_payload_hash = H(request, extracted_values)` and assert `response.payload_hash == expected_payload_hash`. Alternatively, include a commitment to the `request` key (e.g., its hash) in the signed payload and verify it on-chain.

### Proof of Concept

1. Alice submits `verify_foreign_transaction(R_alice)` for Bitcoin tx `T_alice`. The request is stored in `pending_verify_foreign_tx_requests[R_alice]`.
2. The MPC network legitimately processes `R_alice` and produces `(payload_hash_alice, sig_alice)` where `payload_hash_alice = H(R_alice, [BlockHash(block_A)])`. A legitimate participant submits `respond_verify_foreign_tx(R_alice, {payload_hash_alice, sig_alice})`, resolving Alice's yield.
3. Malicious participant Eve observes `(payload_hash_alice, sig_alice)` from the on-chain transaction.
4. Bob submits `verify_foreign_transaction(R_bob)` for a different Bitcoin tx `T_bob`. The request is stored in `pending_verify_foreign_tx_requests[R_bob]`.
5. Eve calls `respond_verify_foreign_tx(R_bob, {payload_hash_alice, sig_alice})`.
6. The contract checks: is `sig_alice` a valid signature over `payload_hash_alice` using the root key? **Yes** — passes.
7. `resolve_yields_for(R_bob, ...)` drains Bob's yield with `{payload_hash_alice, sig_alice}`.
8. Bob's contract receives a `VerifyForeignTransactionResponse` claiming `T_bob` was verified, but the `payload_hash` actually encodes `T_alice`'s data. If Bob's bridge contract does not recompute and verify the `payload_hash`, it releases funds as if `T_bob` was verified — a forged foreign-chain verification. [7](#0-6) [8](#0-7)

### Citations

**File:** crates/contract/src/lib.rs (L600-609)
```rust
                let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");

                // Check the signature is correct
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    payload_hash,
                    &expected_public_key,
                )
                .is_ok()
            }
```

**File:** crates/contract/src/lib.rs (L692-754)
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

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L337-346)
```rust
        let payload = match payload_version {
            dtos::ForeignTxPayloadVersion::V1 => {
                dtos::ForeignTxSignPayload::V1(dtos::ForeignTxSignPayloadV1 {
                    request: request.clone(),
                    values,
                })
            }
            _ => bail!("unsupported payload_version"),
        };
        Ok(payload)
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L124-128)
```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```

**File:** crates/near-mpc-crypto-types/src/sign.rs (L117-125)
```rust
impl SignatureRequest {
    pub fn new(domain: DomainId, payload: Payload, predecessor_id: &AccountId, path: &str) -> Self {
        let tweak = crate::kdf::derive_tweak(predecessor_id, path);
        SignatureRequest {
            domain_id: domain,
            tweak,
            payload,
        }
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
