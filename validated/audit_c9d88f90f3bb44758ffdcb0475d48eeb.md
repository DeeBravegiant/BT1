### Title
Inconsistent Response Validation in `respond_ckd()` Allows a Single Byzantine Participant to Forge CKD Output for `AppPublicKey` Requests — (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_ckd()` skips all cryptographic output verification for `CKDAppPublicKey::AppPublicKey` requests while enforcing it for `CKDAppPublicKey::AppPublicKeyPV` requests. Every other `respond*` function always verifies the cryptographic output before resolving yields. This inconsistency allows a single Byzantine attested participant to submit an arbitrary forged `CKDResponse` for any pending `AppPublicKey` CKD request, permanently delivering an attacker-controlled key derivation output to the user and draining the pending yield slot so the legitimate MPC computation can never respond.

---

### Finding Description

The three `respond*` functions in `MpcContract` apply fundamentally different validation strictness:

**`respond()` — STRICT: always verifies the signature** [1](#0-0) 

**`respond_verify_foreign_tx()` — STRICT: always verifies the signature** [2](#0-1) 

**`respond_ckd()` — INCONSISTENT: verifies only for `AppPublicKeyPV`, skips entirely for `AppPublicKey`**

```rust
// crates/contract/src/lib.rs lines 675-682
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}   // ← NO CHECK AT ALL
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [3](#0-2) 

For `respond()` and `respond_verify_foreign_tx()`, a Byzantine coordinator cannot forge a valid response because the contract cryptographically verifies the output against the MPC public key. For `respond_ckd()` with `AppPublicKey`, there is no such gate — the contract unconditionally calls `resolve_yields_for` with whatever `(big_y, big_c)` the caller supplies. [4](#0-3) 

The pending request is keyed by `(app_public_key, domain_id, predecessor_account_id, derivation_path)`, all of which are publicly visible on-chain from the `request_app_private_key` call. Any single attested participant can observe a pending `AppPublicKey` CKD request and race to call `respond_ckd` with a forged response before the legitimate MPC computation completes. [5](#0-4) 

Because `resolve_yields_for` removes the pending entry from the map, the legitimate MPC response will subsequently fail with `RequestNotFound`, permanently locking in the forged output. [6](#0-5) 

---

### Impact Explanation

**Critical — Confidential key derivation output without the required participant authorization.**

The CKD protocol's security guarantee is that producing a correct output requires threshold-many honest participants to cooperate off-chain. For `AppPublicKey` requests, a single Byzantine attested participant can bypass this threshold requirement entirely by submitting an arbitrary `CKDResponse` on-chain. The attacker can:

1. Deliver a `(big_y, big_c)` pair they constructed, making the user derive a key the attacker already knows — breaking confidentiality of the derived key material.
2. Deliver garbage values, making the user's derived key unusable — breaking availability.
3. Permanently consume the pending yield slot so the legitimate threshold computation can never deliver the correct output.

This directly matches the allowed impact: *"Unauthorized … confidential key derivation output without the required participant authorization."*

---

### Likelihood Explanation

**Medium.** The attacker must be a single attested participant — a legitimate MPC node that has been compromised or is acting maliciously. This is explicitly within the Byzantine fault model (below the signing threshold). The attack requires no special privilege beyond holding a valid TEE attestation, and the target request parameters are fully public on-chain. The attacker only needs to submit their forged `respond_ckd` call before the honest coordinator does. [7](#0-6) 

---

### Recommendation

Apply the same strict validation pattern used by `respond()` and `respond_verify_foreign_tx()`. If the `AppPublicKey` variant's output cannot be verified on-chain with the same `ckd_output_check` (because the contract lacks the app private key), then either:

1. **Require threshold-many `respond_ckd` calls** (one per participant) and accept only when a quorum agrees on the same `(big_y, big_c)` — analogous to how threshold signing works off-chain before a single `respond()` is submitted.
2. **Deprecate `AppPublicKey` in favour of `AppPublicKeyPV`**, which is the only variant the contract can actually verify, and document that `AppPublicKey` provides no on-chain integrity guarantee.
3. At minimum, **add a comment and a security notice** in the ABI documentation that `AppPublicKey` CKD responses are unverified on-chain and that callers must verify the output themselves using their app private key.

---

### Proof of Concept

```
Setup:
  - Alice submits request_app_private_key(AppPublicKey(alice_pk), domain_id, path)
  - Contract stores pending CKD request; all parameters are public on-chain.

Attack:
  - Byzantine attested participant Eve observes the pending request.
  - Eve calls respond_ckd(ckd_request, CKDResponse { big_y: [0u8;48], big_c: [0u8;48] })
  - Contract checks: Eve is an attested participant ✓
  - Contract checks: AppPublicKey variant → NO cryptographic check ✓
  - Contract calls resolve_yields_for → pending entry removed, forged response delivered to Alice.

Result:
  - Alice receives Eve's forged (big_y, big_c) as her derived key material.
  - The legitimate MPC computation's respond_ckd call fails with RequestNotFound.
  - Alice's derived key is attacker-controlled or invalid.
  - respond() and respond_verify_foreign_tx() are immune to this attack because
    they always verify the cryptographic output before resolving yields.
```

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

**File:** crates/contract/src/lib.rs (L653-666)
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
```

**File:** crates/contract/src/lib.rs (L675-682)
```rust
        match &request.app_public_key {
            dtos::CKDAppPublicKey::AppPublicKey(_) => {}
            dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
                if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
                    env::panic_str("CKD output check failed");
                }
            }
        }
```

**File:** crates/contract/src/lib.rs (L684-688)
```rust
        pending_requests::resolve_yields_for(
            &mut self.pending_ckd_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
```

**File:** crates/contract/src/lib.rs (L718-747)
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
