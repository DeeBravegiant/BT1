### Title
Single Byzantine Participant Can Deliver Unverified CKD Response via `respond_ckd` `AppPublicKey` Variant - (File: crates/contract/src/lib.rs)

### Summary
`respond_ckd` skips all cryptographic verification of the CKD output when the request carries the `AppPublicKey` variant. Every other respond path (`respond`, `respond_verify_foreign_tx`) unconditionally verifies the cryptographic output before resolving the yield. A single Byzantine attested participant — strictly below the signing threshold — can call `respond_ckd` with an arbitrary fake `CKDResponse` for any pending `AppPublicKey` request, and the contract will accept and deliver it to the waiting caller without any check.

### Finding Description
`respond` always verifies the signature against the caller-derived public key before resolving the yield:

```rust
// crates/contract/src/lib.rs  lines 597-608
let expected_public_key =
    derive_key_secp256k1(&affine, &request.tweak).map_err(RespondError::from)?;
let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response, payload_hash, &expected_public_key,
).is_ok()
```

`respond_verify_foreign_tx` likewise always verifies the signature against the root public key (lines 718–743) before resolving.

`respond_ckd` (lines 653–689) diverges: verification is gated on the variant of `app_public_key`:

```rust
// crates/contract/src/lib.rs  lines 675-682
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← no check at all
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
```

When the user submitted `request_app_private_key` with `AppPublicKey`, the contract stores the request and parks the yield. Any single attested participant may then call `respond_ckd` supplying that stored request key plus an entirely fabricated `CKDResponse`. Because the `AppPublicKey` arm is a no-op, `resolve_yields_for` is reached unconditionally and the fake response is serialised and delivered to the waiting caller.

The discrepancy is structural: `respond` and `respond_verify_foreign_tx` bind the accepted output to a cryptographic proof tied to the stored request; `respond_ckd` with `AppPublicKey` does not. This is the direct analog of the reference report's pattern — one callback path enforces the invariant, another silently omits it.

### Impact Explanation
The user receives attacker-chosen key material instead of the threshold-computed CKD output. Because the CKD result is used to derive app-specific secrets (private keys, encryption keys, authentication credentials), the attacker knows the fake material and can use it to steal funds, decrypt confidential data, or impersonate the user on any system that trusts the derived key. This breaks the core threshold-security guarantee of the CKD flow for all `AppPublicKey` requests.

### Likelihood Explanation
The attacker must be a single TEE-attested participant — strictly below the reconstruction threshold. No collusion with other participants is required. Pending `request_app_private_key` entries are visible in on-chain contract state, so the attacker can identify targets. The `AppPublicKey` variant is a first-class, documented API path, not an edge case.

### Recommendation
Remove the asymmetry. Either:
1. Require all CKD requests to supply a verifiable public key (`AppPublicKeyPV`) so `ckd_output_check` is always executed, or
2. Derive and store a commitment to the expected output at request time (analogous to how `SignatureRequest` stores the `payload` and `tweak`) and verify the response against it in `respond_ckd` regardless of variant.

If `AppPublicKey` is intentionally trust-based, gate it behind an explicit governance flag and document that it provides no threshold-security guarantee.

### Proof of Concept
1. User calls `request_app_private_key` with `CKDAppPublicKey::AppPublicKey(some_pk)`. Contract stores the `CKDRequest` and parks the yield.
2. Byzantine participant (one of N, below threshold) calls `respond_ckd(stored_request, CKDResponse { /* all zeros or attacker-chosen key */ })`.
3. Contract enters the `AppPublicKey(_) => {}` arm — zero verification — and calls `resolve_yields_for`, serialising the fake response.
4. User's parked call resumes and receives the attacker-controlled `CKDResponse` as if it were the legitimate threshold output.
5. User derives keys from the fake material; attacker, who chose the material, can reproduce every derived key. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
