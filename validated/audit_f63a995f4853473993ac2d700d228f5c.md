### Title
Missing Input Validation on Legacy `AppPublicKey` in `request_app_private_key` Allows Confidential Key Disclosure - (File: crates/contract/src/lib.rs)

### Summary

The `request_app_private_key` endpoint accepts the legacy `CKDAppPublicKey::AppPublicKey` variant (a single BLS12-381 G1 point) with **zero cryptographic validation**. An unprivileged caller can submit the G1 identity point (the zero element) as the app public key, causing the ElGamal encryption in the CKD protocol to degenerate and the raw BLS signature `msk·H(app_id)` to be returned in plaintext — bypassing the confidentiality guarantee of the CKD protocol entirely.

### Finding Description

In `request_app_private_key`, the contract branches on the `app_public_key` variant:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← no validation
    dtos::CKDAppPublicKey::AppPublicKeyPV(pk) => {
        if !app_public_key_check(pk) {
            env::panic_str("app public key check failed")
        }
    }
}
``` [1](#0-0) 

The `AppPublicKeyPV` branch calls `app_public_key_check`, which performs a BLS12-381 pairing check (`e(pk1, g2) = e(g1, pk2)`) to verify the key pair is consistent. The `AppPublicKey` branch does nothing. Any G1 point — including the identity point — is silently accepted and stored in `pending_ckd_requests`.

The same gap exists in `respond_ckd`:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← no validation
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(...) { env::panic_str("CKD output check failed"); }
    }
}
``` [2](#0-1) 

The CKD protocol computes the ElGamal ciphertext as:

```
Y = y · G1
C = msk · H(pk || app_id) + app_pk · y
```

When `app_pk = identity (0·G1)`, the encryption term vanishes: `C = msk · H(pk || app_id)`. The response `big_c` returned on-chain is the raw BLS signature — the confidential key — in plaintext.

The `app_public_key_check` function in `crates/contract/src/primitives/ckd.rs` is only called for `AppPublicKeyPV`; it is never invoked for the legacy `AppPublicKey` variant: [3](#0-2) 

The existing test `app_public_key_check__should_accept_identity_key_pair` documents that even for `AppPublicKeyPV`, identity pairs pass the pairing check (intentionally, per the comment "identity points are accepted in `AppPublicKeyPV` to support use cases where the derived key is intentionally public"). No such design rationale exists for the `AppPublicKey` variant, and no test or guard prevents the identity point from being submitted there. [4](#0-3) 

### Impact Explanation

An unprivileged caller submitting `AppPublicKey(G1::identity())` receives `big_c = msk · H(pk || app_id)` — the raw BLS signature that constitutes the confidential key for their `app_id` — without possessing the corresponding app secret key. The entire confidentiality guarantee of the CKD protocol (that only the holder of the app secret key can decrypt the output) is bypassed. The attacker can then use this key to impersonate the TEE application for their `app_id`, accessing any resource or signing capability gated on that derived secret. This matches the allowed impact: **unauthorized access to secret material that materially enables secret recovery**.

### Likelihood Explanation

The attack requires only a standard NEAR account and a 1 yoctoNEAR deposit. The `AppPublicKey` variant is the documented legacy format, still accepted by the contract and reachable by any caller. No privileged role, threshold collusion, or TEE access is required. The identity point is a valid compressed G1 encoding that passes deserialization without error.

### Recommendation

Add an explicit non-identity check for the `AppPublicKey` variant in both `request_app_private_key` and `respond_ckd`, analogous to the existing `app_public_key_check` for `AppPublicKeyPV`. At minimum, reject the G1 identity point:

```rust
dtos::CKDAppPublicKey::AppPublicKey(pk) => {
    // Reject the identity point: submitting it causes the ElGamal
    // encryption to degenerate and the raw BLS signature to be returned.
    if pk.is_identity() {
        env::panic_str("app public key must not be the identity point");
    }
}
```

Additionally, consider whether any arbitrary G1 point should be accepted, or whether a subgroup membership check (analogous to the pairing check for `AppPublicKeyPV`) should be enforced.

### Proof of Concept

1. Obtain the compressed encoding of the BLS12-381 G1 identity point: `[0xC0, 0x00, ..., 0x00]` (48 bytes, compressed-infinity flag set).
2. Call `request_app_private_key` with:
   ```json
   {
     "request": {
       "derivation_path": "exploit",
       "app_public_key": "bls12381g1:<base58-of-identity-point>",
       "domain_id": <bls12381-domain-id>
     }
   }
   ```
   with 1 yoctoNEAR attached. The contract accepts the request without error.
3. MPC nodes pick up the request and run the CKD protocol with `app_pk = identity`. They compute `C = msk · H(pk || app_id) + identity · y = msk · H(pk || app_id)` and return `(big_c, big_y)`.
4. The caller receives `big_c` = the raw BLS signature `msk · H(pk || app_id)` — the confidential key — in plaintext, without possessing any app secret key. [5](#0-4) [6](#0-5)

### Citations

**File:** crates/contract/src/lib.rs (L469-511)
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

**File:** crates/contract/src/primitives/ckd.rs (L62-74)
```rust
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
