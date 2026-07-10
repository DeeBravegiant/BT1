### Title
Single Attested Participant Can Submit Unverified CKD Response for Legacy `AppPublicKey` Requests - (File: `crates/contract/src/lib.rs`)

### Summary

The `respond_ckd()` function in the MPC contract accepts any `CKDResponse` from a single attested participant for legacy `AppPublicKey`-variant requests without performing any cryptographic output verification. A Byzantine participant strictly below the signing threshold can race to submit an arbitrary response, permanently consuming the pending request and delivering a wrong derived key to the user.

### Finding Description

`respond_ckd()` dispatches on the request's `app_public_key` variant to decide whether to verify the response: [1](#0-0) 

For `AppPublicKeyPV` (publicly verifiable), `ckd_output_check` is called against the BLS12-381 master public key and the user's ephemeral key pair, rejecting any response that does not cryptographically match. For the legacy `AppPublicKey` variant the match arm is an empty block — no check whatsoever is performed. The response is immediately serialised and used to resolve all queued yield indices for that request: [2](#0-1) 

The only gate before this point is `assert_caller_is_attested_participant_and_protocol_active()`, which verifies that the caller is a current active participant with a stored TEE attestation: [3](#0-2) 

A single such participant — one out of `n`, well below the signing threshold `t` — satisfies this gate and can call `respond_ckd` with arbitrary `big_y` / `big_c` BLS12-381 G1 values. The contract accepts the call, removes the pending request from `pending_ckd_requests`, and delivers the fabricated payload to every waiting yield.

Contrast this with `respond()` for ECDSA/EdDSA signatures, where the contract verifies the submitted signature against the MPC public key before resolving yields: [4](#0-3) 

No equivalent on-chain guard exists for the legacy CKD path.

### Impact Explanation

A user who submitted a `request_app_private_key` call with the legacy `AppPublicKey` format receives a `CKDResponse` whose `big_y` and `big_c` fields were chosen by the attacker. The user's client decrypts `big_y` with their ephemeral secret key and obtains a garbage or attacker-chosen derived key. The pending request is permanently consumed — the yield is resolved and the entry is removed from `pending_ckd_requests` — so the user cannot retry with the same parameters. Any assets the user subsequently deposits to an address derived from this wrong key are permanently inaccessible (permanent freezing). This matches the allowed Medium impact: **request-lifecycle and contract execution-flow manipulation that breaks production safety/accounting invariants**.

### Likelihood Explanation

The attacker must be an active, TEE-attested participant — a Byzantine participant strictly below the signing threshold. TEE attestation is checked at `submit_participant_info` time, not at `respond_ckd` time; a node that obtained attestation while running correct code can later deviate and submit a fabricated response. The attack requires no threshold collusion, no leaked keys, and no physical TEE compromise. The attacker simply needs to submit their fabricated `respond_ckd` transaction before the legitimate coordinated response arrives on-chain. Because `pending_ckd_requests` resolves on the first accepted call, a single racing transaction is sufficient.

### Recommendation

Apply the same on-chain output verification to the legacy `AppPublicKey` variant that already exists for `AppPublicKeyPV`. If a publicly verifiable check is not possible for the legacy format by design, the contract should at minimum require that the response be co-signed or attested by a threshold of participants before resolving the yield, mirroring the implicit threshold guarantee that the ECDSA/EdDSA `respond()` path derives from signature verification.

### Proof of Concept

1. User calls `request_app_private_key` with `AppPublicKey` (legacy) variant and attaches 1 yoctonear deposit. The request is stored in `pending_ckd_requests`.
2. Attacker (a single attested participant, account `evil.near`) constructs a `CKDResponse { big_y: [0u8; 48], big_c: [0u8; 48] }` — arbitrary BLS12-381 G1 bytes.
3. Attacker calls `respond_ckd(request, fake_response)` directly from their participant account.
4. The contract passes all checks: `assert_caller_is_signer()`, `is_running_or_resharing()`, `accept_requests`, `assert_caller_is_attested_participant_and_protocol_active()`, and the `AppPublicKey` match arm executes the empty block.
5. `resolve_yields_for` is called; the pending request is removed and the fabricated payload is delivered to the user's yield.
6. The user's client receives `big_y = [0u8; 48]`, decrypts it with their ephemeral secret, and obtains a meaningless derived key. The legitimate MPC response, when it eventually arrives, finds no pending request and is silently dropped. [5](#0-4)

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

**File:** crates/contract/src/lib.rs (L2389-2403)
```rust
    fn assert_caller_is_attested_participant_and_protocol_active(&self) {
        let participants = self.protocol_state.active_participants();

        Self::assert_caller_is_signer();

        let attestation_check = self
            .tee_state
            .is_caller_an_attested_participant(participants);

        assert_matches::assert_matches!(
            attestation_check,
            Ok(()),
            "Caller must be an attested participant"
        );
    }
```
