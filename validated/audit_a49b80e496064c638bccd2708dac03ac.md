I have traced the full attack path through the code. Here is my analysis:

---

### Title
Identity-Point `AppPublicKeyPV` Bypasses ElGamal Encryption, Exposing `msk·H(app_id)` On-Chain — (`crates/threshold-signatures/src/confidential_key_derivation/protocol_pv.rs`, `crates/contract/src/primitives/ckd.rs`)

---

### Summary

An unprivileged caller can submit an `AppPublicKeyPV` request with both points set to the group identity (`G1_identity`, `G2_identity`). Every guard in the pipeline accepts this input. The `ckd_pv` protocol then produces `big_c = msk·H(app_id)` in plaintext (the ElGamal mask term vanishes), and the on-chain `respond_ckd` transaction publishes this value publicly. Any observer can read the BLS signature `msk·H(app_id)` and derive the same secret key that a TEE application would derive, breaking the confidentiality guarantee of the CKD protocol entirely.

---

### Finding Description

**Step 1 — `app_public_key_check` accepts identity points (both contract-side and node-side)**

The contract-side check in `crates/contract/src/primitives/ckd.rs` verifies `e(pk1, -G2)·e(G1, pk2) = 1`. For identity inputs: `e(identity, -G2) = 1` and `e(G1, identity) = 1`, so the product is `1`. The check passes. This is explicitly documented by the existing test: [1](#0-0) 

The node-side `app_public_key_check` in `protocol_pv.rs` first calls `check_valid_point_g1` / `check_valid_point_g2`: [2](#0-1) 

`check_valid_point_g1` only tests `is_on_curve() & is_torsion_free()`: [3](#0-2) 

The identity element is on the curve and is torsion-free (it is the neutral element of the prime-order subgroup), so this returns `true`. The subsequent pairing check also evaluates to `1`. Neither check calls `is_identity()`.

**Step 2 — `compute_signature_share` produces `big_c = big_s` when `pk1 = identity`** [4](#0-3) 

`big_c = big_s + app_pk.pk1 * y`. With `app_pk.pk1 = identity`, `identity * y = identity` (the neutral element), so `big_c = big_s = hash_point * private_share`. After Lagrange-interpolated aggregation across all threshold participants, the coordinator obtains `big_C = msk · H(pk, app_id)` — the raw BLS signature — with no masking.

**Step 3 — `aggregated_output_check` passes** [5](#0-4) 

The check verifies `e(big_c, -G2) · e(big_y, pk2) · e(H, pk) = 1`. With `pk2 = identity` and `big_c = msk·H`:
- `e(msk·H, -G2) · e(big_y, identity) · e(H, msk·G2)`
- `= e(H, -msk·G2) · 1 · e(H, msk·G2) = 1` ✓

**Step 4 — `respond_ckd` and `ckd_output_check` pass** [6](#0-5) 

The contract's `ckd_output_check` performs the identical pairing equation: [7](#0-6) 

With `pk2 = identity`, the `e(big_y, pk2)` term collapses to `1`, and the check passes for the same reason as Step 3. The `respond_ckd` transaction is accepted and `big_c = msk·H(app_id)` is stored and returned on-chain.

---

### Impact Explanation

The CKD protocol's confidentiality rests entirely on the ElGamal encryption: `big_c = msk·H + a·Y` where `a` is the app's private scalar. Setting `pk1 = identity` removes the masking term, so `big_c` is the plaintext BLS signature `msk·H(app_id)`. Any observer of the `respond_ckd` transaction reads this value directly and can compute `s = HKDF(msk·H(app_id))` — the exact secret key the TEE application would derive. The attacker can do this for any `app_id` of their choosing, impersonating any TEE application's key derivation without being inside a TEE.

This is unauthorized access to secret material (the per-app BLS signature) that materially enables secret recovery for any CKD-protected application.

---

### Likelihood Explanation

The attack requires only a standard NEAR contract call to `request_app_private_key` with a crafted `AppPublicKeyPV` struct containing compressed encodings of the G1 and G2 identity points. No threshold collusion, no TEE access, no special privileges are needed. The identity point encodings are well-defined and publicly known. The existing test suite explicitly documents that identity pairs are accepted.

---

### Recommendation

Add an explicit identity-point rejection in both `app_public_key_check` implementations:

- **Node-side** (`protocol_pv.rs`): after the `check_valid_point_g1`/`check_valid_point_g2` calls, add `if app_pk.pk1.is_identity().into() || app_pk.pk2.is_identity().into() { return false; }`.
- **Contract-side** (`ckd.rs`): after decompression, check that neither `pk1` nor `pk2` is the identity before calling `bls12381_pairing_check`.

The same guard should be applied to `big_y` in `aggregated_output_check` and `ckd_output_check` (a zero `big_y` also trivially satisfies the equation regardless of `big_c`).

---

### Proof of Concept

1. Encode `G1_identity` (48-byte compressed BLS12-381 G1 identity: `0x40` followed by 47 zero bytes) and `G2_identity` (96-byte compressed G2 identity: `0xc0` followed by 95 zero bytes).
2. Call `request_app_private_key({ derivation_path: "x", app_public_key: { AppPublicKeyPV: { pk1: G1_identity, pk2: G2_identity } }, domain_id: <bls_ckd_domain> })`.
3. Wait for the MPC network to process the request and emit a `respond_ckd` transaction.
4. Read `big_c` from the transaction. Verify: `e(big_c, G2) == e(H(pk, app_id), network_pk)`. If true, `big_c = msk·H(app_id)` is confirmed.
5. Compute `s = HKDF(big_c)` to obtain the TEE application's derived secret key.

### Citations

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

**File:** crates/threshold-signatures/src/confidential_key_derivation/protocol_pv.rs (L207-211)
```rust
    // S <- x . H(app_id)
    let big_s = hash_point * private_share.to_scalar();

    // C <- S + y . A
    let big_c = big_s + app_pk.pk1 * y.0;
```

**File:** crates/threshold-signatures/src/confidential_key_derivation/protocol_pv.rs (L221-236)
```rust
/// Check that `e(big_c, g2) = e(big_y, app_pk2) . e(hash_point, public_key)`
fn aggregated_output_check(
    output: &CKDOutput,
    app_pk: &PublicVerificationKey,
    public_key: &VerifyingKey,
    hash_point: &ElementG1,
) -> bool {
    if !check_valid_point_g1(output.big_c.into()) || !check_valid_point_g1(output.big_y.into()) {
        return false;
    }
    multi_miller_loop(&[
        (output.big_c, -ElementG2::generator()),
        (output.big_y, app_pk.pk2),
        (*hash_point, public_key.to_element()),
    ])
}
```

**File:** crates/threshold-signatures/src/confidential_key_derivation/protocol_pv.rs (L238-247)
```rust
/// Check that `e(app_pk1, g2) = e(g1, app_pk2)`
fn app_public_key_check(app_pk: &PublicVerificationKey) -> bool {
    if !check_valid_point_g1(app_pk.pk1.into()) || !check_valid_point_g2(app_pk.pk2.into()) {
        return false;
    }
    multi_miller_loop(&[
        (app_pk.pk1, -ElementG2::generator()),
        (ElementG1::generator(), app_pk.pk2),
    ])
}
```

**File:** crates/threshold-signatures/src/confidential_key_derivation/ciphersuite.rs (L219-221)
```rust
pub(crate) fn check_valid_point_g1(p: G1Affine) -> bool {
    (p.is_on_curve() & p.is_torsion_free()).into()
}
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
