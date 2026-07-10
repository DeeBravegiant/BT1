### Title
Non-PV CKD Response Accepted Without `app_id` Binding — Byzantine Participant Can Deliver Cross-App Derived Key — (`crates/contract/src/lib.rs`)

---

### Summary

`respond_ckd` performs zero cryptographic verification for the `AppPublicKey` (non-PV) variant. Any single Byzantine attested participant can race the honest coordinator and deliver a `CKDResponse` whose `big_y`/`big_c` were computed for an attacker-controlled `app_id_B`, resolving the victim's pending request for `app_id_A`. The victim's app decrypts to `msk·H(pk‖app_id_B)` — a value the attacker already knows — instead of the correct `msk·H(pk‖app_id_A)`.

---

### Finding Description

In `respond_ckd`, the match on `app_public_key` is:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← no check at all
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For the PV variant, `ckd_output_check` enforces the pairing equation `e(big_c, g2) = e(big_y, app_pk2)·e(H(pk‖app_id), msk·g2)`, which cryptographically binds the response to the exact `app_id` in the request. [2](#0-1) 

For the non-PV variant, the arm is an empty block. `resolve_yields_for` is then called unconditionally with the caller-supplied `response` bytes, resuming every queued yield for the `CKDRequest` key. [3](#0-2) 

`resolve_yields_for` itself performs no content validation — it simply calls `promise_yield_resume` for every queued data-id and removes the map entry. [4](#0-3) 

The only gate is `assert_caller_is_attested_participant_and_protocol_active`, a **single-node** check (not a threshold check). Any one attested participant may call `respond_ckd` for any pending non-PV request. [5](#0-4) 

---

### Impact Explanation

The CKD output for the non-PV variant is `big_c = msk·H(pk‖app_id) + y·app_pk`, `big_y = y·G`. The app decrypts with its private key `a` (where `app_pk = a·G`) as `big_c − a·big_y = msk·H(pk‖app_id)`.

If the attacker substitutes a response computed for `app_id_B` but re-randomised with `app_pk_A`:

```
big_c_attack = msk·H(pk‖app_id_B) + y'·app_pk_A
big_y_attack = y'·G
```

The victim decrypts: `big_c_attack − a_A·big_y_attack = msk·H(pk‖app_id_B)`.

The attacker already knows `msk·H(pk‖app_id_B)` (obtained by running the honest MPC for their own app and decrypting with their own private key `a_B`). The victim's derived secret is therefore fully predictable to the attacker, enabling unauthorized key prediction and cross-app key confusion. [6](#0-5) 

---

### Likelihood Explanation

The attacker needs:
1. **One Byzantine attested MPC participant** — within the standard Byzantine threshold model.
2. **Control of any app** (`app_id_B`) — trivial; any NEAR account can call `request_app_private_key`.
3. **Knowledge of `app_pk_A`** — public, visible on-chain in the pending request.
4. **Race the honest coordinator** — the attacker monitors the pending-request map and calls `respond_ckd` before the honest coordinator. Since `resolve_yields_for` removes the entry on first resolution, whichever call arrives first wins.

No threshold collusion, no TEE compromise, and no network-level DoS is required.

---

### Recommendation

Add a non-PV output check analogous to `ckd_output_check`. For the non-PV variant the app's G2 key is not available on-chain, but the contract can verify the weaker statement that `big_c − big_y·0 = msk·H(pk‖app_id)` using a single pairing: `e(big_c, g2) = e(big_y, 0) · e(H(pk‖app_id), msk·g2)` — i.e., `e(big_c − big_y·app_pk, g2) = e(H(pk‖app_id), public_key)`. Alternatively, require threshold-many `respond_ckd` calls (one per participant) and aggregate on-chain before resolving yields, mirroring the threshold model used for signing.

---

### Proof of Concept

```
// Setup: attacker controls Byzantine node N_B and app with app_id_B, private key a_B.

// Step 1 – attacker obtains msk·H(pk‖app_id_B):
//   Submit request_app_private_key(app_id_B, app_pk_B).
//   Honest MPC runs; coordinator submits respond_ckd for app_id_B.
//   Attacker's app callback receives (big_c_B, big_y_B).
//   Attacker computes S_B = big_c_B − a_B·big_y_B  =  msk·H(pk‖app_id_B).

// Step 2 – victim submits request_app_private_key(app_id_A, app_pk_A).
//   Pending entry: CKDRequest { app_public_key: AppPublicKey(app_pk_A),
//                               app_id: app_id_A, domain_id: D }

// Step 3 – Byzantine node N_B races the honest coordinator:
//   y' ← random scalar
//   big_y_attack = y'·G
//   big_c_attack = S_B + y'·app_pk_A          // bound to app_id_B, not app_id_A
//   N_B calls respond_ckd(
//       request  = CKDRequest { app_public_key: AppPublicKey(app_pk_A),
//                               app_id: app_id_A, domain_id: D },
//       response = CKDResponse { big_y: big_y_attack, big_c: big_c_attack }
//   )
//   → AppPublicKey arm: no check → resolve_yields_for accepts → victim yield resumed.

// Step 4 – victim's app decrypts:
//   big_c_attack − a_A·big_y_attack
//   = S_B + y'·app_pk_A − a_A·y'·G
//   = S_B                                      // = msk·H(pk‖app_id_B)
//   Attacker already knows S_B → victim's derived secret is fully known to attacker.
```

### Citations

**File:** crates/contract/src/lib.rs (L666-666)
```rust
        self.assert_caller_is_attested_participant_and_protocol_active();
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

**File:** crates/contract/src/lib.rs (L684-688)
```rust
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

**File:** crates/contract/src/pending_requests.rs (L66-88)
```rust
pub(crate) fn resolve_yields_for<K>(
    requests: &mut LookupMap<K, Vec<YieldIndex>>,
    request: &K,
    response_bytes: Vec<u8>,
) -> Result<(), Error>
where
    K: BorshSerialize + BorshDeserialize + Clone + Ord,
{
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
}
```

**File:** crates/contract/tests/sandbox/utils/sign_utils.rs (L436-450)
```rust
    let app_id = derive_app_id(account_id, derivation_path);
    let app_pk: ckd::ElementG1 = app_public_key
        .try_into()
        .expect("invalid BLS12-381 G1 point");
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
