### Title
Missing CKD Response Verification for `AppPublicKey` Variant in `respond_ckd` vs. Full Verification in `respond` — (File: `crates/contract/src/lib.rs`)

### Summary
The `respond_ckd` function skips all cryptographic verification of the CKD output when the request uses the `AppPublicKey` (legacy) variant, while the analogous `respond` function always verifies the signature before resolving any yield. A single malicious attested participant (Byzantine node strictly below the signing threshold) can call `respond_ckd` with an arbitrary forged `CKDResponse` for any pending `AppPublicKey` CKD request, and the contract will accept it and deliver the wrong derived key to the user.

### Finding Description

`respond` (signature path) always performs on-chain cryptographic verification before resolving the yield: [1](#0-0) 

If the signature does not verify, the call is rejected: [2](#0-1) 

`respond_ckd` (CKD path) performs verification **only** for the `AppPublicKeyPV` variant. For the `AppPublicKey` (legacy) variant, the match arm is an empty no-op: [3](#0-2) 

After this match, `resolve_yields_for` is called unconditionally, delivering whatever `CKDResponse` the caller supplied: [4](#0-3) 

The only gate before this point is `assert_caller_is_signer()` and `assert_caller_is_attested_participant_and_protocol_active()` — i.e., the caller must be a single attested participant, not a threshold-many quorum: [5](#0-4) 

### Impact Explanation

A single Byzantine attested participant (below the signing threshold) can:

1. Observe a pending `AppPublicKey` CKD request in `pending_ckd_requests`.
2. Construct an arbitrary `CKDResponse` (e.g., a response encrypting a key of the attacker's choice).
3. Call `respond_ckd` with that forged response.
4. The contract accepts it and resolves the user's yield with the wrong derived key.

The user's application receives a derived key that was not produced by the honest MPC threshold computation. This breaks the core confidential key derivation guarantee — the user's derived key is now under attacker control, enabling decryption of data the user encrypted under the expected key, or impersonation in protocols that rely on the derived key. This matches the allowed impact: **confidential key derivation output without the required participant authorization**.

### Likelihood Explanation

**Medium.** The attacker must be a single attested participant (a node that has passed TEE attestation). This is a realistic threat: a single compromised or malicious node operator below the signing threshold can execute this attack without any collusion. The `AppPublicKey` (legacy) variant is still supported and used in production per the contract README. [6](#0-5) 

### Recommendation

Apply the same defense-in-depth principle used in `respond`: either:

1. **Reject `AppPublicKey` in `respond_ckd`** — prohibit the legacy variant from being submitted via `respond_ckd` and require callers to migrate to `AppPublicKeyPV`, which supports on-chain verification; or
2. **Add an equivalent on-chain check for `AppPublicKey`** — if a publicly verifiable check is not possible for the legacy variant by design, document this explicitly and add a comment explaining the trust assumption, and consider whether the legacy path should be deprecated.

The inconsistency between `respond` (always verifies) and `respond_ckd` (conditionally verifies) is the root cause and should be resolved to make the security model uniform across all response paths.

### Proof of Concept

```
1. User calls request_app_private_key with AppPublicKey variant → pending CKD request stored.
2. Attacker (single attested participant) calls respond_ckd(request, forged_response).
3. Contract executes:
   - assert_caller_is_signer() → passes (attacker is attested)
   - is_running_or_resharing() → passes
   - accept_requests → passes
   - assert_caller_is_attested_participant_and_protocol_active() → passes
   - match AppPublicKey => {} (no-op, no verification)
   - resolve_yields_for(..., forged_response) → user yield resolved with attacker-chosen key
4. User receives forged derived key; attacker controls the key material.
``` [7](#0-6)

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
