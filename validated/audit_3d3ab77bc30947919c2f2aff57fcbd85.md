### Title
Missing BLS12-381 G1 Identity Point Validation in `request_app_private_key` (`AppPublicKey` Variant) - (File: crates/contract/src/lib.rs)

### Summary
The `request_app_private_key` function validates the `AppPublicKeyPV` variant of `app_public_key` but performs **no validation** for the `AppPublicKey` variant. An unprivileged caller can submit the BLS12-381 G1 identity point (the cryptographic "zero") as `app_public_key`, causing the MPC network to return the derived secret `big_s` in plaintext as `big_c`, breaking the confidentiality invariant of the CKD protocol.

### Finding Description

In `request_app_private_key`, the validation block is asymmetric:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}   // ← NO VALIDATION
    dtos::CKDAppPublicKey::AppPublicKeyPV(pk) => {
        if !app_public_key_check(pk) {
            env::panic_str("app public key check failed")
        }
    }
}
``` [1](#0-0) 

The `AppPublicKey` arm is a no-op — it accepts any BLS12-381 G1 encoding, including the identity point. The `AppPublicKeyPV` arm calls `app_public_key_check`, which verifies pairing consistency of `(pk1, pk2)`.

The CKD protocol computes the response as:
- `big_s = H(app_id, pk_mpc) * msk` (the derived secret)
- `big_y = G1 * y` (random ephemeral)
- `big_c = big_s + app_pk * y` (ElGamal-encrypted secret) [2](#0-1) 

If `app_pk = G1::identity()` (the zero point), then `big_c = big_s + 0·y = big_s`. The on-chain response `(big_y, big_c)` directly exposes the derived secret `big_s` in plaintext.

This is the exact analog of the reported "missing zero address check": a function accepting a cryptographic key argument without validating it is not the identity/zero element. The `AppPublicKeyPV` variant explicitly documents that identity key pairs satisfy the pairing equation and are accepted for a specific intentional use case ("where the derived key is intentionally public"): [3](#0-2) 

No such intentional design exists for the `AppPublicKey` variant — it simply has no check at all.

The `derive_from_path` function used to compute `app_id` uses a comma separator to prevent hash-extension attacks, but this does not protect against a zero `app_pk`: [4](#0-3) 

The `SignRequestArgs` deserialization tests even explicitly confirm that an empty `path` string is accepted without rejection: [5](#0-4) 

### Impact Explanation

The CKD protocol's security invariant is that `big_s` (the derived secret) is only decryptable by the holder of private key `a` (where `app_pk = a·G1`). By submitting `app_pk = G1::identity()`, a caller bypasses this requirement and receives `big_s` in plaintext in the on-chain response.

The CKD protocol is designed for TEE applications: the TEE generates `(a, A)` inside the enclave, submits `A`, and decrypts `big_s = big_c − a·big_y` inside the TEE. The security guarantee is that `big_s` is only accessible inside the TEE. Submitting `app_pk = 0` allows any account holder to extract `big_s` without being inside the TEE, breaking TEE isolation for the CKD flow.

The derived secret `big_s` is scoped to the caller's own `app_id` (derived from their account ID and derivation path), so the attacker cannot extract another account's secret. However, the confidentiality invariant of the CKD protocol — that `big_s` is only decryptable by the holder of `a` — is broken for the caller's own derived key material.

**Impact**: Medium — breaks the production safety invariant of CKD (confidentiality of the derived secret) without enabling theft of another party's funds or unauthorized signing.

### Likelihood Explanation

Any unprivileged caller can submit `app_public_key = G1::identity()` in the `AppPublicKey` variant of `request_app_private_key`. The only prerequisite is a 1 yoctonear deposit. No special privileges, collusion, or key material is required. The attack is a single contract call. [6](#0-5) 

### Recommendation

Add an identity-point rejection for the `AppPublicKey` variant, mirroring the validation already present for `AppPublicKeyPV`:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(pk) => {
        // Reject the identity point — submitting it would reveal big_s in plaintext.
        let decompressed = env::bls12381_p1_decompress(pk);
        let identity_uncompressed = G1Projective::identity().to_uncompressed();
        if decompressed == identity_uncompressed.as_slice() {
            env::panic_str("app public key must not be the identity point");
        }
    }
    dtos::CKDAppPublicKey::AppPublicKeyPV(pk) => {
        if !app_public_key_check(pk) {
            env::panic_str("app public key check failed")
        }
    }
}
```

The `app_public_key_check` function already uses `env::bls12381_p1_decompress`, which aborts on malformed encodings, so the same host function can be reused here. [7](#0-6) 

### Proof of Concept

1. Encode the BLS12-381 G1 identity point in compressed form (48 bytes with the infinity flag set).
2. Call `request_app_private_key` with:
   ```json
   {
     "request": {
       "derivation_path": "any-path",
       "app_public_key": "bls12381g1:<identity-point-base58>",
       "domain_id": <ckd-domain-id>
     }
   }
   ```
   with 1 yoctonear attached.
3. The contract accepts the request (no validation on `AppPublicKey` arm).
4. The MPC network computes `big_c = big_s + identity * y = big_s`.
5. The on-chain response contains `big_c = big_s` in plaintext.
6. The attacker reads `big_s` directly from the response without holding any private key, bypassing the TEE isolation requirement of the CKD protocol. [8](#0-7)

### Citations

**File:** crates/contract/src/lib.rs (L469-512)
```rust
    pub fn request_app_private_key(&mut self, request: CKDRequestArgs) {
        log!(
            "request_app_private_key: predecessor={:?}, request={:?}",
            env::predecessor_account_id(),
            request
        );

        let domain_id: DomainId = request.domain_id;
        let (_, predecessor) = self.check_request_preconditions(
            domain_id,
            DomainPurpose::CKD,
            Gas::from_tgas(self.config.ckd_call_gas_attachment_requirement_tera_gas),
            MINIMUM_CKD_REQUEST_DEPOSIT,
        );

        match &request.app_public_key {
            dtos::CKDAppPublicKey::AppPublicKey(_) => {}
            dtos::CKDAppPublicKey::AppPublicKeyPV(pk) => {
                if !app_public_key_check(pk) {
                    env::panic_str("app public key check failed")
                }
            }
        }

        let request = CKDRequest::new(
            request.app_public_key,
            domain_id,
            &predecessor,
            &request.derivation_path,
        );

        let callback_gas = Gas::from_tgas(
            self.config
                .return_ck_and_clean_state_on_success_call_tera_gas,
        );

        let callback_args = serde_json::to_vec(&(&request,)).unwrap();
        self.enqueue_yield_request(
            method_names::RETURN_CK_AND_CLEAN_STATE_ON_SUCCESS,
            callback_args,
            callback_gas,
            move |this, id| this.add_ckd_request(request, id),
        );
    }
```

**File:** crates/contract/src/primitives/ckd.rs (L17-31)
```rust
impl CKDRequest {
    pub fn new(
        app_public_key: dtos::CKDAppPublicKey,
        domain_id: DomainId,
        predecessor_id: &AccountId,
        derivation_path: &str,
    ) -> Self {
        let app_id = derive_app_id(predecessor_id, derivation_path);
        Self {
            app_public_key,
            app_id,
            domain_id,
        }
    }
}
```

**File:** crates/contract/src/primitives/ckd.rs (L56-74)
```rust
/// Check that `e(app_pk1, g2) = e(g1, app_pk2)`.
///
/// Point validation is fully delegated to the host: the decompression
/// functions abort execution on malformed or off-curve encodings, and
/// `bls12381_pairing_check` returns `false` when a point is outside its
/// prime-order subgroup.
pub(crate) fn app_public_key_check(app_public_key: &dtos::CKDAppPublicKeyPV) -> bool {
    let pk1 = env::bls12381_p1_decompress(&app_public_key.pk1);
    let pk2 = env::bls12381_p2_decompress(&app_public_key.pk2);

    let pairing_input = [
        pk1.as_slice(),
        MINUS_G2_GENERATOR_UNCOMPRESSED.as_slice(),
        G1_GENERATOR_UNCOMPRESSED.as_slice(),
        pk2.as_slice(),
    ]
    .concat();
    env::bls12381_pairing_check(&pairing_input)
}
```

**File:** crates/contract/src/primitives/ckd.rs (L479-495)
```rust
    /// Documents the pre-existing behavior that identity key pairs satisfy
    /// the pairing equation and are accepted.
    #[test]
    #[expect(non_snake_case)]
    fn app_public_key_check__should_accept_identity_key_pair() {
        // Given
        let app_pk = dtos::CKDAppPublicKeyPV {
            pk1: dtos::Bls12381G1PublicKey(G1Projective::identity().to_compressed()),
            pk2: dtos::Bls12381G2PublicKey(G2Projective::identity().to_compressed()),
        };

        // When
        let accepted = app_public_key_check(&app_pk);

        // Then
        assert!(accepted);
    }
```

**File:** crates/contract/src/primitives/ckd.rs (L497-530)
```rust
    /// Builds a CKD output that satisfies
    /// `e(big_c, g2) = e(big_y, app_pk2) . e(hash_point, public_key)`.
    fn make_valid_ckd_output(
        rng: &mut StdRng,
    ) -> (
        dtos::CkdAppId,
        CKDResponse,
        dtos::CKDAppPublicKeyPV,
        dtos::Bls12381G2PublicKey,
    ) {
        let msk = Scalar::random(&mut *rng);
        let network_pk =
            dtos::Bls12381G2PublicKey((G2Projective::generator() * msk).to_compressed());

        let app_scalar = Scalar::random(&mut *rng);
        let app_pk1 = G1Projective::generator() * app_scalar;
        let app_pk = make_app_public_key_pv(app_scalar);

        let app_id = derive_app_id(&"alice.near".parse().unwrap(), "path");
        let hash_point = G1Projective::hash_to_curve(
            &[network_pk.0.as_slice(), app_id.as_ref()].concat(),
            NEAR_CKD_DOMAIN,
            &[],
        );

        let y = Scalar::random(&mut *rng);
        let big_y = G1Projective::generator() * y;
        let big_c = hash_point * msk + app_pk1 * y;
        let response = CKDResponse {
            big_y: dtos::Bls12381G1PublicKey(big_y.to_compressed()),
            big_c: dtos::Bls12381G1PublicKey(big_c.to_compressed()),
        };
        (app_id, response, app_pk, network_pk)
    }
```

**File:** crates/near-mpc-crypto-types/src/kdf.rs (L25-39)
```rust
fn derive_from_path(derivation_prefix: &str, predecessor_id: &AccountId, path: &str) -> [u8; 32] {
    // TODO: Use a key derivation library instead of doing this manually.
    // https://crates.io/crates/hkdf might be a good option?
    //
    // ',' is ACCOUNT_DATA_SEPARATOR from nearcore that indicate the end
    // of the account id in the trie key. We reuse the same constant to
    // indicate the end of the account id in derivation path.
    // Do not reuse this hash function on anything that isn't an account
    // ID or it'll be vulnerable to Hash Malleability/extension attacks.
    let derivation_path = format!("{derivation_prefix}{},{}", predecessor_id, path);
    let mut hasher = Sha3_256::new();
    hasher.update(derivation_path);
    let hash: [u8; 32] = hasher.finalize().into();
    hash
}
```

**File:** crates/near-mpc-crypto-types/src/sign.rs (L355-369)
```rust
    #[test]
    fn deserialize__should_accept_empty_path() {
        // Given
        let json = serde_json::json!({
            "path": "",
            "payload_v2": {"Ecdsa": ecdsa_payload_hex()},
            "domain_id": 0
        });

        // When
        let args: SignRequestArgs = serde_json::from_value(json).unwrap();

        // Then
        assert_eq!(args.path, "");
    }
```
