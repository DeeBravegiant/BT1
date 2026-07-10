### Title
Missing Response Verification in `respond_ckd` for `AppPublicKey` Variant Allows Single Byzantine Participant to Forge Confidential Key Derivation Output - (File: `crates/contract/src/lib.rs`)

---

### Summary

The `respond_ckd` function in the MPC smart contract verifies the CKD response cryptographically only for the `AppPublicKeyPV` (publicly verifiable) variant. For the `AppPublicKey` (privately verifiable) variant, the contract performs **no verification** of the response before resolving all pending yields. A single Byzantine MPC participant acting as the signing leader can call `respond_ckd` with an arbitrary, attacker-crafted `CKDResponse`, and the contract will accept it and deliver the forged key to the requesting user — without threshold authorization.

---

### Finding Description

In `crates/contract/src/lib.rs`, the `respond_ckd` function handles both CKD variants:

```rust
// lines 653–689
pub fn respond_ckd(&mut self, request: CKDRequest, response: CKDResponse) -> Result<(), Error> {
    let signer = Self::assert_caller_is_signer();
    // ...
    self.assert_caller_is_attested_participant_and_protocol_active();
    // ...
    match &request.app_public_key {
        dtos::CKDAppPublicKey::AppPublicKey(_) => {}   // ← NO VERIFICATION
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
``` [1](#0-0) 

For `AppPublicKeyPV`, `ckd_output_check` verifies the pairing relation between the response and the user's public key, so a forged response is rejected on-chain. For `AppPublicKey`, the arm is a no-op — any `CKDResponse` value is accepted unconditionally and immediately delivered to all queued callers via `resolve_yields_for`. [2](#0-1) 

By contrast, the analogous `respond` function for threshold signatures **always** verifies the signature cryptographically before resolving yields: [3](#0-2) 

This asymmetry means the `AppPublicKey` CKD path has no on-chain guard equivalent to what protects the signature path.

---

### Impact Explanation

The `AppPublicKey` variant is "privately verifiable": only the requesting app (holding the corresponding secret key) can verify the decrypted result. Because the contract cannot verify the response on-chain, it accepts whatever the leader submits. A single Byzantine leader can:

1. Intercept a pending `request_app_private_key` call using `AppPublicKey`.
2. Construct a `CKDResponse { big_y, big_c }` where `big_c` encrypts a key of the attacker's choosing under the user's public key.
3. Call `respond_ckd` — passing access control as an attested participant — with the forged response.
4. The contract resolves all queued yields with the forged output.

The user's app receives a key that the attacker knows, breaking the confidentiality guarantee of CKD entirely. This constitutes **confidential key derivation output without the required participant (threshold) authorization** — a single participant below the signing threshold can unilaterally forge the output.

**Allowed impact matched:** *Critical — Unauthorized confidential key derivation output without the required participant authorization.*

---

### Likelihood Explanation

- The leader role rotates among participants per request; any participant can be leader for a given CKD request.
- A single Byzantine participant (strictly below the signing threshold) is sufficient — no collusion is required.
- The `AppPublicKey` variant is the legacy/default path and is actively used.
- The attacker needs only to be an attested MPC participant, which is a realistic adversary model for a Byzantine-fault-tolerant system.

---

### Recommendation

Apply the same on-chain verification discipline to `AppPublicKey` responses that already exists for `AppPublicKeyPV`. Since `AppPublicKey` is privately verifiable (the contract cannot check the pairing), the recommended mitigations are:

1. **Deprecate `AppPublicKey` in favor of `AppPublicKeyPV`**, which provides on-chain verifiability and is already protected by `ckd_output_check`.
2. **Or require threshold-quorum submission**: require at least `threshold` distinct attested participants to submit the same `CKDResponse` before resolving yields, analogous to how threshold signing requires threshold participation before a signature is valid.
3. At minimum, add explicit documentation that `AppPublicKey` CKD requests are not protected against a Byzantine leader and that users requiring confidentiality guarantees must use `AppPublicKeyPV`.

---

### Proof of Concept

1. User calls `request_app_private_key` with `AppPublicKey(pk)` and attaches 1 yoctoNEAR deposit. A yield is queued in `pending_ckd_requests`.
2. Byzantine leader (an attested participant) observes the pending request on-chain.
3. Leader constructs `CKDResponse { big_y: G, big_c: Enc(pk, attacker_chosen_key) }` — a value that encrypts a key the attacker knows.
4. Leader calls `respond_ckd(ckd_request, forged_response)`. The contract passes `assert_caller_is_attested_participant_and_protocol_active()`, enters the `AppPublicKey(_) => {}` arm (no check), and calls `resolve_yields_for`, delivering the forged response to the user.
5. The user's app decrypts `big_c` with its secret key and obtains `attacker_chosen_key` — a key the attacker already knows — instead of the MPC-derived secret. The confidentiality guarantee is broken with no on-chain evidence of the forgery. [4](#0-3)

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
