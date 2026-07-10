### Title
Missing CKD Output Verification for `AppPublicKey` Variant Allows Single Byzantine Participant to Forge Derived Key Material - (File: `crates/contract/src/lib.rs`)

---

### Summary

In `respond_ckd`, the `ckd_output_check` cryptographic pairing verification is placed exclusively inside the `AppPublicKeyPV` branch of a match on `request.app_public_key`. The `AppPublicKey` branch is an empty no-op. A single attested Byzantine participant can therefore call `respond_ckd` with an entirely fabricated `CKDResponse` for any pending `AppPublicKey` request, and the contract will accept and deliver it without any cryptographic check.

---

### Finding Description

`respond_ckd` dispatches on the two variants of `CKDAppPublicKey`:

```rust
// crates/contract/src/lib.rs  lines 675-682
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← no check
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

`ckd_output_check` verifies the BLS12-381 pairing equation `e(big_c, g2) = e(big_y, app_pk2) · e(H(pk, app_id), mpc_pk)`, which cryptographically binds the response to the network's master secret key and the user's ephemeral key. [2](#0-1) 

For `AppPublicKey` (a single G1 point, the legacy "privately verifiable" format), no analogous check exists. The `AppPublicKey` arm is a literal empty block. After passing the `assert_caller_is_attested_participant_and_protocol_active` guard — which requires only that the caller is **one** attested participant — the response is serialised and delivered unconditionally via `resolve_yields_for`. [3](#0-2) 

By contrast, `respond` (ECDSA/EdDSA) and `respond_verify_foreign_tx` both perform full cryptographic signature verification before resolving any yield: [4](#0-3) [5](#0-4) 

The `AppPublicKey` variant is the default format used in node integration tests and is actively supported in production: [6](#0-5) 

---

### Impact Explanation

The user's derived secret is computed client-side as:

```
secret = big_c − app_sk · big_y
```

where `app_sk` is the user's private scalar (unknown to the attacker). If the attacker sets `big_y = G1_identity` (the identity point, a valid G1 element), then `app_sk · big_y = identity`, and:

```
secret = big_c − identity = big_c
```

The attacker chose `big_c` freely, so they know `secret` exactly. They can then derive the same symmetric key as the user (e.g. via HKDF), decrypting any data the user protected with that key. This constitutes **unauthorized confidential key derivation output** — the attacker learns the user's derived secret without the required threshold of participant authorization.

The contract performs no identity-point check on `big_y` and no consistency check on `big_c` for the `AppPublicKey` path.

---

### Likelihood Explanation

- The `AppPublicKey` variant is the legacy default and is in active production use.
- Only **one** attested participant is required to call `respond_ckd` — no threshold collusion is needed.
- The attacker must be an attested MPC participant, which is a realistic Byzantine threat model explicitly considered by the system's TEE attestation design.
- The attack is silent: the user receives a well-formed `CKDResponse` and only discovers the forgery when they attempt to use the derived key.

---

### Recommendation

For `AppPublicKey` requests, the contract cannot apply the pairing-based `ckd_output_check` because the G2 component (`pk2`) is absent. Two mitigations are possible:

1. **Deprecate and remove `AppPublicKey` support** in `respond_ckd` (and correspondingly in `request_app_private_key`), requiring all callers to use `AppPublicKeyPV`. The README already labels `AppPublicKey` as "legacy".

2. **If `AppPublicKey` must be retained**, document explicitly that responses for this variant carry no on-chain integrity guarantee, and move the security responsibility entirely to the client. At minimum, add a comment in the `AppPublicKey(_) => {}` arm explaining the intentional absence of a check, so future reviewers do not mistake it for an oversight.

The structural fix mirrors the RubiconMarket recommendation: the guard (`ckd_output_check`) must either be applied on all code paths or the unguarded path must be eliminated.

---

### Proof of Concept

1. User calls `request_app_private_key` with `AppPublicKey(g1 * app_sk)` and `derivation_path = "mykey"`. A `CKDRequest` is stored and a yield is created.

2. Byzantine participant `P` (a single attested node) constructs a forged response:
   - `big_y = G1_identity` (compressed encoding of the BLS12-381 G1 identity point)
   - `big_c = G1 * k` for any scalar `k` chosen by `P`

3. `P` calls `respond_ckd(ckd_request, forged_response)`.

4. The contract executes lines 675-682: the `AppPublicKey(_) => {}` arm fires, no check is performed.

5. `resolve_yields_for` delivers `forged_response` to the waiting yield.

6. The user receives `(big_c = G1*k, big_y = identity)` and computes:
   ```
   secret = G1*k − app_sk · identity = G1*k
   ```

7. `P` knows `k`, computes `G1*k`, and derives the identical symmetric key via HKDF, fully recovering the user's confidential derived key. [7](#0-6) [2](#0-1) [8](#0-7)

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

**File:** crates/contract/src/primitives/ckd.rs (L80-102)
```rust
pub(crate) fn ckd_output_check(
    app_id: &dtos::CkdAppId,
    output: &CKDResponse,
    app_public_key: &dtos::CKDAppPublicKeyPV,
    public_key: &dtos::Bls12381G2PublicKey,
) -> bool {
    let big_c = env::bls12381_p1_decompress(&output.big_c);
    let big_y = env::bls12381_p1_decompress(&output.big_y);
    let pk2 = env::bls12381_p2_decompress(&app_public_key.pk2);
    let pk = env::bls12381_p2_decompress(public_key);
    let hash_point = hash_app_id_with_pk(public_key.as_slice(), app_id.as_ref());

    let pairing_input = [
        big_c.as_slice(),
        MINUS_G2_GENERATOR_UNCOMPRESSED.as_slice(),
        big_y.as_slice(),
        pk2.as_slice(),
        hash_point.as_slice(),
        pk.as_slice(),
    ]
    .concat();
    env::bls12381_pairing_check(&pairing_input)
}
```

**File:** crates/node/src/tests.rs (L376-381)
```rust
    let app_public_key = near_mpc_contract_interface::types::CKDAppPublicKey::AppPublicKey(
        "bls12381g1:6KtVVcAAGacrjNGePN8bp3KV6fYGrw1rFsyc7cVJCqR16Zc2ZFg3HX3hSZxSfv1oH6"
            .parse()
            .unwrap(),
    );
    do_request_ckd_and_await_response(indexer, user, domain, timeout_sec, app_public_key).await
```

**File:** crates/near-mpc-crypto-types/src/ckd.rs (L15-18)
```rust
pub enum CKDAppPublicKey {
    AppPublicKey(Bls12381G1PublicKey),
    AppPublicKeyPV(CKDAppPublicKeyPV),
}
```
