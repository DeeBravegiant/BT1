Let me examine the relevant code to evaluate this claim.

### Title
Single Byzantine Attested Participant Can Unilaterally Resolve All Fan-Out CKD Yields With Fabricated Key Material — (`crates/contract/src/lib.rs`, `crates/contract/src/pending_requests.rs`)

### Summary

`respond_ckd` performs **no cryptographic verification** on the `CKDResponse` when the request uses the `AppPublicKey` variant. A single attested participant can call it with arbitrary fabricated `big_c`/`big_y` values, and `resolve_yields_for` will atomically drain every queued yield (up to `MAX_PENDING_REQUEST_FAN_OUT = 128`) with that fabricated response. All subsequent honest `respond_ckd` calls return `RequestNotFound` because the map entry has already been removed.

---

### Finding Description

**Entry point — `respond_ckd` (lib.rs:654–689):**

The function checks that the caller is one attested participant, then branches on the `app_public_key` variant:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← no verification
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For `AppPublicKey`, the arm is a no-op. Any `CKDResponse` — including one with attacker-chosen `big_c` and `big_y` — passes unconditionally.

**Fan-out drain — `resolve_yields_for` (pending_requests.rs:66–88):**

```rust
let resumed = requests
    .remove(request)          // ← removes the entire queue atomically
    .unwrap_or_default()
    .into_iter()
    .map(|YieldIndex { data_id }| {
        env::promise_yield_resume(&data_id, response_bytes.clone());
    })
    .count();

if resumed > 0 { Ok(()) } else { Err(InvalidParameters::RequestNotFound.into()) }
``` [2](#0-1) 

`remove` is called once; the map entry is gone. Every subsequent `respond_ckd` call for the same request key hits the `resumed == 0` branch and returns `RequestNotFound`.

**Fan-out cap:** [3](#0-2) 

Up to 128 duplicate submissions can be queued under one key, all resolved in the single attacker call.

---

### Impact Explanation

The attacker chooses `big_c = r·G₁` and `big_y = s·G₁` for scalars `r, s` they know. Every caller who submitted a duplicate `AppPublicKey` CKD request under the same `(predecessor_id, derivation_path, domain_id)` key receives this fabricated response. Because the attacker knows the discrete log of `big_c`, they know the derived key material the caller will use. This enables the attacker to forge signatures or transactions on behalf of every affected caller — matching the Critical impact: *unauthorized confidential key derivation output without the required participant authorization*.

---

### Likelihood Explanation

The attacker must be a single Byzantine attested participant — strictly below the signing threshold. Attested participants observe all on-chain requests in real time. NEAR transaction ordering is deterministic and publicly visible, so the attacker can reliably race honest nodes by submitting `respond_ckd` in the same block or the immediately following one. No collusion, leaked keys, or network-level interference is required.

---

### Recommendation

Apply the same pairing-equation check to `AppPublicKey` that is already applied to `AppPublicKeyPV`, or require threshold-many matching `respond_ckd` submissions before resolving yields (analogous to how threshold signatures require t-of-n agreement before a valid signature can be produced). The `AppPublicKeyPV` path already demonstrates the correct pattern via `ckd_output_check`. [4](#0-3) 

---

### Proof of Concept

1. Submit `MAX_PENDING_REQUEST_FAN_OUT` (128) identical `AppPublicKey` CKD requests from distinct callers, all sharing the same `(predecessor_id, derivation_path, domain_id)`.
2. From one Byzantine attested participant account, call `respond_ckd` with a fabricated `CKDResponse { big_c: r·G₁, big_y: s·G₁ }` where `r, s` are attacker-chosen scalars.
3. Assert: all 128 yield-resume callbacks deliver the fabricated response.
4. Call `respond_ckd` again from an honest participant with the correct response.
5. Assert: the call returns `RequestNotFound` — honest nodes are permanently locked out.
6. Verify: the attacker, knowing `r`, can reconstruct the derived key and forge operations on behalf of all 128 callers.

### Citations

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

**File:** crates/contract/src/pending_requests.rs (L37-37)
```rust
pub const MAX_PENDING_REQUEST_FAN_OUT: u8 = 128;
```

**File:** crates/contract/src/pending_requests.rs (L74-87)
```rust
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
