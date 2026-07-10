### Title
Missing CKD Output Validation for `AppPublicKey` Variant Allows Byzantine Participant to Forge Derived Key - (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_ckd` enforces a cryptographic pairing check on `CKDAppPublicKey::AppPublicKeyPV` responses but performs **no equivalent check** on `CKDAppPublicKey::AppPublicKey` responses. A single Byzantine attested participant — strictly below the signing threshold — can call `respond_ckd` with an arbitrary forged `CKDResponse`, causing the contract to deliver an attacker-controlled key to the requesting user instead of the legitimately threshold-derived key.

---

### Finding Description

In `respond_ckd`, the contract branches on the request's `app_public_key` variant:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← no check
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For `AppPublicKeyPV`, `ckd_output_check` verifies the pairing equation `e(big_c, g2) = e(big_y, app_pk2) · e(hash_point, public_key)`, which cryptographically binds the response to the MPC network's public key and the user's app public key — ensuring the response was produced using the actual threshold secret. [2](#0-1) 

For `AppPublicKey`, the arm is a no-op (`{}`). The contract immediately proceeds to `resolve_yields_for`, delivering whatever `big_y` and `big_c` the caller supplied. [3](#0-2) 

The legitimate CKD computation for `AppPublicKey` produces:

```
big_s = H(mpc_pk, app_id) * msk
big_y = g1 * y          (random blinding)
big_c = big_s + app_pk * y
``` [4](#0-3) 

Without an on-chain check, any attested participant can substitute arbitrary `big_y` and `big_c` values. The user's derived secret `big_c − big_y * x_user` then equals an attacker-chosen value rather than the legitimate `big_s`.

The `AppPublicKeyPV` variant has this protection precisely because the pairing check is the only way to verify correctness without knowing the user's private key. The `AppPublicKey` variant is equally vulnerable to substitution — the contract simply omits the guard.

---

### Impact Explanation

A single Byzantine attested participant (below the signing threshold) can:

1. Observe a pending `AppPublicKey` CKD request in the contract's `pending_ckd_requests` map.
2. Race to call `respond_ckd` with a forged `CKDResponse { big_y: g1*a, big_c: g1*b }` for arbitrary scalars `a`, `b`.
3. The contract accepts the response — the `AppPublicKey` branch performs no check — and `resolve_yields_for` delivers the forged key material to the user.
4. The user's application derives a secret from attacker-controlled points. If the attacker chose `a` and `b` such that they know the resulting discrete-log relationship, they can recover or predict the user's derived secret.

This is unauthorized confidential key derivation output delivered without threshold participant authorization — the threshold MPC protocol is bypassed entirely for this variant.

**Impact: Critical** — matches "Unauthorized … confidential key derivation output without the required participant authorization."

---

### Likelihood Explanation

The attacker must be an attested MPC participant (TEE attestation required). However, only **one** such participant is needed — no threshold collusion. The attack is a simple race: submit `respond_ckd` before the honest leader. Because `resolve_yields_for` drains the queue on the first valid call, the first responder wins.

**Likelihood: Medium** — requires a single compromised/malicious attested node, which is a realistic adversary model for a Byzantine participant strictly below threshold.

---

### Recommendation

Add an equivalent output check for the `AppPublicKey` variant. For `AppPublicKey(app_pk)`, verify that the response satisfies the CKD protocol relation using the app's G1 public key and the MPC network's G2 public key. Concretely, verify:

```
e(big_c, g2) = e(H(mpc_pk, app_id), mpc_pk_g2) · e(big_y, app_pk_g2)
```

where `app_pk_g2` is derived from `app_pk` (G1) via a fixed mapping, or alternatively require callers to supply both G1 and G2 components (as `AppPublicKeyPV` already does) so the pairing check is always applicable. Until a check is in place, the `AppPublicKey` variant should be treated as unverified output.

---

### Proof of Concept

```
1. User calls request_app_private_key({
       app_public_key: AppPublicKey(legitimate_g1_pk),
       derivation_path: "path",
       domain_id: bls_domain_id,
   })
   → contract stores CKDRequest in pending_ckd_requests

2. Byzantine attested participant calls respond_ckd(
       request = <same CKDRequest>,
       response = CKDResponse {
           big_y: Bls12381G1PublicKey([0x01; 48]),  // attacker-chosen
           big_c: Bls12381G1PublicKey([0x02; 48]),  // attacker-chosen
       }
   )

3. Contract executes lib.rs:675-682:
       AppPublicKey(_) => {}   ← no check, falls through

4. resolve_yields_for delivers forged CKDResponse to user's yield promise.

5. User receives big_y=[0x01;48], big_c=[0x02;48] — attacker-controlled key material —
   instead of the legitimately threshold-derived output.
``` [5](#0-4) [6](#0-5)

### Citations

**File:** crates/contract/src/lib.rs (L675-688)
```rust
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
```

**File:** crates/contract/src/primitives/ckd.rs (L76-102)
```rust
/// Check that `e(big_c, g2) = e(big_y, app_pk2) . e(hash_point, public_key)`.
///
/// Point validation is fully delegated to the host, as in
/// [`app_public_key_check`].
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

**File:** crates/contract/tests/sandbox/utils/sign_utils.rs (L440-450)
```rust
    let msk = key_package.private_share.to_scalar();

    let big_s = hash_app_id_with_pk(&key_package.public_key, app_id.as_ref()) * msk;
    let y = ckd::Scalar::random(OsRng);
    let big_y = ckd::ElementG1::generator() * y;
    let big_c = big_s + app_pk * y;

    let response = CKDResponse {
        big_y: (&big_y).into(),
        big_c: (&big_c).into(),
    };
```
