### Title
Missing CKD Response Verification for `AppPublicKey` Variant Allows Single Byzantine Participant to Deliver Forged Key Derivation Output - (File: `crates/contract/src/lib.rs`)

---

### Summary

The `respond_ckd` function applies a cryptographic output check only for the `AppPublicKeyPV` variant of a CKD request, leaving the `AppPublicKey` variant with an empty match arm and no verification. Because `respond` (ECDSA/EdDSA) always verifies the signature cryptographically before accepting it, the threshold requirement is enforced there by the math. For `respond_ckd` with `AppPublicKey`, no equivalent proof exists, so a single attested-but-Byzantine participant can submit an arbitrary `CKDResponse` that the contract accepts and delivers to the requesting user — bypassing the threshold-signature requirement for that derivation.

---

### Finding Description

In `respond_ckd` (lines 653–689 of `crates/contract/src/lib.rs`), after confirming the caller is an attested participant, the contract branches on the request's `app_public_key` field:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← no verification
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For `AppPublicKeyPV`, `ckd_output_check` verifies that the submitted `CKDResponse` is consistent with the app's verifiable public key and the MPC root key, providing on-chain proof that the derivation was computed correctly. For `AppPublicKey`, the arm is empty — the contract immediately proceeds to `resolve_yields_for`, delivering whatever bytes the caller supplied. [2](#0-1) 

Compare this with `respond` (ECDSA/EdDSA), which always verifies the signature cryptographically against the derived public key before resolving any queued yields: [3](#0-2) 

The ECDSA/EdDSA signature itself is the proof that threshold-many nodes participated in the computation. No analogous proof is required or checked for `AppPublicKey` CKD responses.

The only gate protecting `respond_ckd` is `assert_caller_is_attested_participant_and_protocol_active()`: [4](#0-3) 

This confirms the caller is a single attested participant — not that threshold-many nodes agreed on the output.

---

### Impact Explanation

A single Byzantine participant (attested, but below the signing threshold) can:

1. Monitor the NEAR chain for `request_app_private_key` calls that use the `AppPublicKey` variant.
2. Race honest nodes by calling `respond_ckd` with an arbitrary, attacker-chosen `CKDResponse`.
3. The contract accepts the forged response without any cryptographic check and resumes all queued yield promises with the attacker's bytes.
4. The requesting user receives a wrong derived key. If they use that key to control funds on a foreign chain (the primary use-case for CKD), those funds are at risk of theft or permanent loss.

This constitutes **unauthorized confidential key derivation output without the required participant authorization** — a Critical impact under the allowed scope.

---

### Likelihood Explanation

- The attacker needs only to be one of the current attested participants (a Byzantine node below threshold).
- No collusion with other participants is required.
- The race condition is straightforward: the Byzantine node submits `respond_ckd` before honest nodes do. Because NEAR processes transactions in block order, the first valid `respond_ckd` call drains the queue; subsequent calls from honest nodes return `RequestNotFound`.
- The `AppPublicKey` variant is a production code path (not test-only), reachable by any user calling `request_app_private_key`. [5](#0-4) 

---

### Recommendation

Add cryptographic verification for the `AppPublicKey` variant in `respond_ckd`, equivalent to what `respond` does for ECDSA/EdDSA. If on-chain verification of BLS12-381 CKD output is not feasible without the verifiable public key, the contract should at minimum:

1. Require a threshold-weighted quorum of `respond_ckd` calls (collect votes, accept only when threshold is reached), analogous to how `vote_pk` / `vote_reshared` accumulate votes before acting.
2. Or deprecate the `AppPublicKey` variant for production use and require `AppPublicKeyPV` for all CKD requests where the output will control real assets.

---

### Proof of Concept

1. Deploy the MPC contract in Running state with N participants (threshold T < N).
2. User calls `request_app_private_key` with `app_public_key = AppPublicKey(some_pk)`.
3. Byzantine participant (participant index < T) immediately calls:
   ```
   respond_ckd(request, CKDResponse { /* attacker-chosen fake output */ })
   ```
4. The contract passes the `AppPublicKey(_) => {}` branch with no check, calls `resolve_yields_for`, and resumes the user's yield with the forged response.
5. Honest nodes' subsequent `respond_ckd` calls return `RequestNotFound` — the queue is already drained.
6. The user's callback receives the attacker-controlled derived key material. [6](#0-5)

### Citations

**File:** crates/contract/src/lib.rs (L586-644)
```rust
        let signature_is_valid = match (&response, public_key) {
            (
                dtos::SignatureResponse::Secp256k1(signature_response),
                PublicKeyExtended::Secp256k1 { near_public_key },
            ) => {
                // generate the expected public key
                let secp_pk = dtos::Secp256k1PublicKey::try_from(&near_public_key)
                    .expect("Secp256k1 variant always has a secp256k1 key");
                let affine = *k256::PublicKey::try_from(&secp_pk)
                    .expect("stored key is always valid")
                    .as_affine();
                let expected_public_key =
                    derive_key_secp256k1(&affine, &request.tweak).map_err(RespondError::from)?;

                let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");

                // Check the signature is correct
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    payload_hash,
                    &expected_public_key,
                )
                .is_ok()
            }
            (
                dtos::SignatureResponse::Ed25519 { signature },
                PublicKeyExtended::Ed25519 {
                    edwards_point: public_key_edwards_point,
                    ..
                },
            ) => {
                let derived_public_key_edwards_point = derive_public_key_edwards_point_ed25519(
                    &public_key_edwards_point,
                    &request.tweak,
                );
                let derived_public_key_32_bytes =
                    dtos::Ed25519PublicKey::from(derived_public_key_edwards_point.compress());

                let message = request.payload.as_eddsa().expect("Payload is not EdDSA");

                near_mpc_signature_verifier::verify_eddsa_signature(
                    signature,
                    message,
                    &derived_public_key_32_bytes,
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
```

**File:** crates/contract/src/lib.rs (L653-689)
```rust
    #[handle_result]
    pub fn respond_ckd(&mut self, request: CKDRequest, response: CKDResponse) -> Result<(), Error> {
        let signer = Self::assert_caller_is_signer();
        log!("respond_ckd: signer={}, request={:?}", &signer, &request);

        if !self.protocol_state.is_running_or_resharing() {
            return Err(InvalidState::ProtocolStateNotRunning.into());
        }

        if !self.accept_requests {
            return Err(TeeError::TeeValidationFailed.into());
        }

        self.assert_caller_is_attested_participant_and_protocol_active();

        let PublicKeyExtended::Bls12381 {
            public_key: dtos::PublicKey::Bls12381(public_key),
        } = self.public_key_extended(request.domain_id)?
        else {
            env::panic_str("Domain is not compatible with CKD (expected Bls12381 curve)");
        };

        match &request.app_public_key {
            dtos::CKDAppPublicKey::AppPublicKey(_) => {}
            dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
                if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
                    env::panic_str("CKD output check failed");
                }
            }
        }

        pending_requests::resolve_yields_for(
            &mut self.pending_ckd_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
    }
```

**File:** crates/contract/src/pending_requests.rs (L62-88)
```rust
/// Resume every yield queued for `request` with `response_bytes`, draining the
/// fan-out map in one pass. Returns `Err(RequestNotFound)` if the map held no entry.
///
/// Resuming a yield that has already timed out is a no-op at the SDK level.
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
