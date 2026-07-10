### Title
Inconsistent Response Validation in `respond_ckd` Allows Single Byzantine Participant to Forge Confidential Key Derivation Output - (File: crates/contract/src/lib.rs)

### Summary

The `respond_ckd` function applies cryptographic output validation only for the `AppPublicKeyPV` (publicly verifiable) variant of CKD requests, but performs **no validation whatsoever** for the `AppPublicKey` (privately verifiable, legacy) variant. In contrast, `respond` always validates the signature against the expected derived public key before resolving pending yields. This inconsistency means a single attested participant — strictly below the signing threshold — can submit an arbitrary forged CKD response for any `AppPublicKey` request, bypassing the threshold requirement entirely and delivering an attacker-controlled key to the user.

### Finding Description

Three parallel "respond" entry points exist in the contract:

**`respond()`** — always validates the response: [1](#0-0) 

The contract derives the expected public key with the per-request tweak and verifies the signature. A forged signature is cryptographically impossible without threshold key shares.

**`respond_ckd()`** — validation is conditional on the request variant: [2](#0-1) 

For `AppPublicKeyPV`, `ckd_output_check` is called and a forged response is rejected. For `AppPublicKey`, the match arm is an empty block — **any** `CKDResponse` value is accepted unconditionally.

**`resolve_yields_for`** is then called in both cases, resolving all queued yields for that request with whatever response was supplied: [3](#0-2) 

The first caller to invoke `respond_ckd` wins; all pending yields for that request key are drained with the supplied response. Because the contract cannot distinguish a legitimately computed CKD output from a fabricated one for the `AppPublicKey` variant, a single Byzantine participant can race honest nodes and deliver a key of the attacker's choosing.

The inconsistency is structural: `respond()` achieves implicit threshold enforcement through signature verification (a valid signature requires threshold shares), while `respond_ckd` with `AppPublicKey` has no equivalent enforcement mechanism.

### Impact Explanation

A single attested participant (strictly below the signing threshold) can:

1. Monitor the chain for a pending `request_app_private_key` call using the `AppPublicKey` variant.
2. Construct a `CKDResponse` that encrypts an attacker-known private key under the user's `app_public_key`.
3. Call `respond_ckd` before honest nodes, passing the attested-participant check at line 666.
4. The contract resolves all queued yields with the forged response.
5. The user receives a derived key that the attacker already knows, enabling theft of any assets controlled by that key on foreign chains.

This is **unauthorized confidential key derivation output without the required participant authorization** — a Critical impact under the allowed scope.

### Likelihood Explanation

- The `AppPublicKey` (legacy) variant is still accepted in production per the contract README.
- Any single attested participant — a realistic Byzantine assumption — can execute this attack.
- The attacker only needs to submit their transaction before honest nodes, which is feasible given NEAR's public mempool and the fact that honest nodes must first complete the off-chain MPC computation before submitting.
- No collusion above threshold is required.

### Recommendation

Apply the same implicit threshold enforcement to `AppPublicKey` CKD responses that `respond()` achieves for signatures. Two options:

1. **Require `AppPublicKeyPV` for all new requests** and reject `AppPublicKey` in `respond_ckd` (deprecate the legacy path).
2. **Require threshold agreement off-chain before any node submits `respond_ckd`**, and enforce this on-chain by collecting votes (similar to `vote_pk` / `vote_reshared`) before resolving yields — so no single node can unilaterally resolve a CKD request.

### Proof of Concept

```
1. User calls request_app_private_key({
       app_public_key: AppPublicKey(user_bls_pk),
       derivation_path: "my-path",
       domain_id: 0
   }) with 1 yoctoNEAR deposit.

2. Attacker (single attested participant) constructs:
       forged_response = CKDResponse {
           big_y: encrypt(attacker_known_key, user_bls_pk),
           big_c: arbitrary_commitment,
       }

3. Attacker calls respond_ckd(ckd_request, forged_response).
   - assert_caller_is_attested_participant_and_protocol_active() passes (attacker is attested).
   - is_running_or_resharing() passes.
   - accept_requests check passes.
   - AppPublicKey branch: empty block, no validation.
   - resolve_yields_for drains all pending yields with forged_response.

4. User's promise resolves with forged_response.
   User decrypts with their app secret key and receives attacker_known_key.
   Attacker can now sign transactions on behalf of the user's derived address.
``` [4](#0-3) [5](#0-4)

### Citations

**File:** crates/contract/src/lib.rs (L563-651)
```rust
    #[handle_result]
    pub fn respond(
        &mut self,
        request: SignatureRequest,
        response: dtos::SignatureResponse,
    ) -> Result<(), Error> {
        let signer = Self::assert_caller_is_signer();

        log!("respond: signer={}, request={:?}", &signer, &request);

        self.assert_caller_is_attested_participant_and_protocol_active();

        if !self.protocol_state.is_running_or_resharing() {
            return Err(InvalidState::ProtocolStateNotRunning.into());
        }

        if !self.accept_requests {
            return Err(TeeError::TeeValidationFailed.into());
        }

        let domain = request.domain_id;
        let public_key = self.public_key_extended(domain)?;

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

        pending_requests::resolve_yields_for(
            &mut self.pending_signature_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
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
