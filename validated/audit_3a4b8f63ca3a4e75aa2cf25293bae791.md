### Title
Missing CKD Output Verification for `AppPublicKey` Variant Enables Single Compromised Participant to Deliver Forged Key Material - (File: `crates/contract/src/lib.rs`)

---

### Summary

In `respond_ckd`, when a confidential key derivation (CKD) request uses the `AppPublicKey` (non-publicly-verifiable, legacy) format, the contract performs **zero cryptographic verification** of the response. A single compromised attested participant — strictly below the signing threshold — can call `respond_ckd` with a fabricated `CKDResponse` for any pending CKD request, delivering attacker-controlled key material to the user. This is a direct analog to the "infinite approval" class: an overly permissive persistent authorization surface that a single privileged-but-sub-threshold actor can exploit without threshold collusion.

---

### Finding Description

`respond_ckd` in `crates/contract/src/lib.rs` handles responses to `request_app_private_key` calls. The function enforces that the caller is an attested participant, but then branches on the request's `app_public_key` variant:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}   // ← no verification whatsoever
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For the `AppPublicKeyPV` variant, `ckd_output_check` cryptographically verifies the encrypted output against the BLS12-381 public key pair. For the `AppPublicKey` (legacy, still accepted) variant, the arm is an empty block — the `CKDResponse` is accepted unconditionally and immediately delivered to the waiting caller via `resolve_yields_for`. [2](#0-1) 

Contrast this with `respond`, which always verifies the ECDSA/EdDSA signature cryptographically before resolving any yield: [3](#0-2) 

The `AppPublicKey` path has no equivalent guard. The contract's README explicitly describes `AppPublicKey` as "legacy" but still supported: [4](#0-3) 

The attacker-controlled entry path is:

1. A user calls `request_app_private_key` with `AppPublicKey` format. The request enters `pending_ckd_requests` keyed by `CKDRequest`.
2. A single compromised attested participant observes the pending request on-chain.
3. Before the legitimate MPC leader submits the real response, the attacker calls `respond_ckd` with the correct `request` key but a fabricated `CKDResponse` — e.g., a ciphertext encrypting a key the attacker already knows under the user's `app_public_key`.
4. The contract passes `assert_caller_is_attested_participant_and_protocol_active()`, skips the empty `AppPublicKey` arm, and calls `resolve_yields_for`, draining the pending queue and delivering the forged response to the user. [5](#0-4) 

Once the first `respond_ckd` succeeds, `resolve_yields_for` removes the entry from `pending_ckd_requests`, so the legitimate MPC response subsequently fails with `RequestNotFound` — the forged response is the only one the user ever receives. [6](#0-5) 

---

### Impact Explanation

The `CKDResponse` is an encrypted confidential key derived from the MPC network's BLS12-381 root key. If the attacker substitutes a ciphertext encrypting a key they control, the user decrypts it and begins using a key the attacker also possesses. This enables the attacker to:

- **Impersonate the user** in any application that trusts the derived key (e.g., sign transactions on behalf of the user).
- **Steal funds** if the derived key controls assets on any chain.

This matches the allowed critical impact: *"Unauthorized transaction execution, threshold signature issuance, or confidential key derivation output without the required participant authorization."*

---

### Likelihood Explanation

- Requires **one** compromised attested participant — strictly below the signing threshold. TEE attestation raises the bar but does not eliminate the risk: a software vulnerability in the MPC image, a supply-chain compromise, or an operator key leak suffices.
- The attacker must front-run the legitimate leader's `respond_ckd` submission. On NEAR, transaction ordering within a block is deterministic; the attacker can submit their fabricated response in the same or an earlier block than the leader.
- The `AppPublicKey` variant is still accepted by the contract (described as "legacy" but not deprecated or gated), so any user or SDK that does not explicitly use `AppPublicKeyPV` is exposed.

---

### Recommendation

1. **Remove or gate the `AppPublicKey` variant.** If it is truly legacy, reject it at the contract level (`check_request_preconditions` or at the top of `respond_ckd`) so no new requests can use it.
2. **If `AppPublicKey` must remain**, add a cryptographic output check analogous to `ckd_output_check` for the PV variant. For the non-PV case this is harder (the verifier cannot check the plaintext), but at minimum the contract should require a threshold-signed commitment over the response before accepting it — mirroring how `respond` requires a valid ECDSA/EdDSA signature.
3. **Document the security gap** prominently so integrators know that `AppPublicKey` provides weaker guarantees than `AppPublicKeyPV`.

---

### Proof of Concept

```
// Setup: contract in Running state, one attested participant = attacker
// User submits a CKD request with AppPublicKey (legacy format)
user.call("request_app_private_key", {
    derivation_path: "m/0",
    app_public_key: { AppPublicKey: "<bls12381g1:USER_EPHEMERAL_PK>" },
    domain_id: 4
}).deposit(1 yoctoNEAR);

// Attacker observes the pending CKDRequest key on-chain
// Attacker constructs a CKDResponse encrypting a key THEY control
// under the user's app_public_key

attacker.call("respond_ckd", {
    request: <the pending CKDRequest>,
    response: <CKDResponse encrypting attacker-known key>
});
// → assert_caller_is_attested_participant_and_protocol_active() passes (attacker is attested)
// → AppPublicKey arm: empty, no verification
// → resolve_yields_for delivers forged response to user
// → pending_ckd_requests entry removed; legitimate MPC response fails with RequestNotFound

// User decrypts the response and obtains a key the attacker already knows.
// Attacker can now sign arbitrary transactions on behalf of the user.
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

**File:** crates/contract/README.md (L118-120)
```markdown
- `app_public_key`: the ephemeral public key for the CKD request. Two formats are supported:
  - **Privately verifiable** (legacy): a single G1 point, e.g. `"bls12381g1:<base58>"` or `{"AppPublicKey": "bls12381g1:<base58>"}`.
  - **Publicly verifiable**: a pair of points `(pk1, pk2) = (a·G1, a·G2)`, passed as `{"AppPublicKeyPV": {"pk1": "bls12381g1:<base58>", "pk2": "bls12381g2:<base58>"}}`. This allows anyone to verify the encrypted result on-chain without the app's secret key.
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
