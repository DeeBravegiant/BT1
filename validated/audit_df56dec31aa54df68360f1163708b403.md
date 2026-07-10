### Title
Missing Cryptographic Output Verification in `respond_ckd` for `AppPublicKey` Variant Allows Single Byzantine Participant to Forge CKD Response - (File: `crates/contract/src/lib.rs`)

---

### Summary

The `respond_ckd` function in `MpcContract` performs cryptographic output verification only for the `AppPublicKeyPV` variant of `CKDAppPublicKey`. For the `AppPublicKey` variant, no verification is performed. A single Byzantine attested participant (strictly below the signing threshold) can call `respond_ckd` with an arbitrary fabricated `CKDResponse`, and the contract will accept it and deliver the forged confidential key derivation output to the requesting user — bypassing the threshold requirement entirely.

---

### Finding Description

In `respond_ckd`, the response validation is asymmetric across the two `CKDAppPublicKey` variants:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}   // ← NO CHECK
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For `AppPublicKeyPV`, `ckd_output_check` verifies the response against the app's public key pair, ensuring the output is cryptographically bound to the MPC network's BLS12-381 key. For `AppPublicKey`, the arm is a no-op — any `CKDResponse` with arbitrary `big_y` and `big_c` fields passes through unconditionally. [2](#0-1) 

After this non-check, `resolve_yields_for` is called unconditionally, which removes the pending request from the map and calls `env::promise_yield_resume` to deliver the response to every queued caller: [3](#0-2) 

By contrast, `respond` (for ECDSA/EdDSA signatures) always verifies the signature against the derived public key before calling `resolve_yields_for`: [4](#0-3) 

This is the structural analog to the external report's bug: the `respond_ckd` function updates the pending-request state (removes the queue entry, resumes all yields) without first validating the invariant that the response is cryptographically correct — just as the original `queue::remove` updated some state (head) but omitted updating the tail.

---

### Impact Explanation

A single Byzantine attested participant (one node, below the T-of-N threshold) can:

1. Observe a live `CKDRequest` with `AppPublicKey` variant in `pending_ckd_requests`.
2. Call `respond_ckd` with fabricated `big_y` and `big_c` values (e.g., the BLS12-381 identity point or any arbitrary group element).
3. The contract accepts the call — participant check passes, no output check is performed.
4. `resolve_yields_for` drains the entire fan-out queue, delivering the forged CKD output to every caller who submitted the same request. [5](#0-4) 

The user receives a derived key that is not the output of the threshold CKD protocol. If the user uses this key to derive a wallet address, encrypt data, or authenticate, the result is controlled by the attacker. This constitutes **unauthorized confidential key derivation output without the required participant authorization** — a Critical impact under the allowed scope.

---

### Likelihood Explanation

The attacker must be an attested participant (TEE attestation required). However, the threshold for CKD is T-of-N; a single node below threshold suffices to exploit this. The `AppPublicKey` variant is the default, non-PV CKD path used in production (e.g., `CKDAppPublicKey::AppPublicKey` is the variant used in the standard `request_app_private_key` flow). The attack requires no collusion, no network-level access, and no privileged operator role beyond holding a valid TEE attestation as a participant. [6](#0-5) 

---

### Recommendation

Add cryptographic output verification for the `AppPublicKey` variant in `respond_ckd`, analogous to the check already present for `AppPublicKeyPV`. Since the `AppPublicKey` variant does not carry a G2 component for on-chain pairing verification, the contract should either:

1. Require the `AppPublicKey` variant to also supply a verifiable commitment (i.e., migrate to `AppPublicKeyPV` for all new requests), or
2. Reject `respond_ckd` calls for `AppPublicKey` requests that arrive without a threshold-attested multi-party proof, or
3. At minimum, verify that `big_y` is a valid non-identity point on BLS12-381 G1 and that `big_c` satisfies the CKD relation against the MPC network's root public key using the same pairing check as `ckd_output_check`. [7](#0-6) 

---

### Proof of Concept

1. Deploy the contract in `Running` state with a BLS12-381 CKD domain.
2. A user calls `request_app_private_key` with `CKDAppPublicKey::AppPublicKey(some_g1_pk)`. The request is queued in `pending_ckd_requests`.
3. A single Byzantine attested participant calls:
   ```
   respond_ckd(
     request = <the pending CKDRequest>,
     response = CKDResponse {
       big_y: Bls12381G1PublicKey([0u8; 48]),  // identity / garbage
       big_c: Bls12381G1PublicKey([0u8; 48]),
     }
   )
   ```
4. The contract executes lines 675–682: the `AppPublicKey` arm is a no-op, no check fires.
5. `resolve_yields_for` at line 684 removes the entry and resumes the user's yield with the forged response.
6. The user's transaction resolves with the fabricated `CKDResponse` — one Byzantine participant below threshold has forged a CKD output. [8](#0-7) [9](#0-8)

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
