### Title
Missing CKD Response Validity Check for `AppPublicKey` Variant Allows Byzantine Participant to Deliver Attacker-Controlled Key Material - (File: `crates/contract/src/lib.rs`)

---

### Summary

`MpcContract::respond_ckd` performs no cryptographic verification of the `CKDResponse` when the pending request uses the `AppPublicKey` variant. A single Byzantine attested participant (strictly below signing threshold) can call `respond_ckd` with arbitrary `big_y` / `big_c` values, causing the contract to deliver attacker-controlled key material to the waiting user. The `AppPublicKeyPV` variant has an equivalent on-chain check (`ckd_output_check`), but the `AppPublicKey` branch is an empty no-op.

---

### Finding Description

In `respond_ckd` the contract branches on the `app_public_key` variant of the stored `CKDRequest`:

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

For `AppPublicKeyPV`, `ckd_output_check` enforces the pairing equation `e(big_c, g2) = e(big_y, app_pk2) · e(hash_point, public_key)`, binding the response to the MPC master key and the user's app identity. For `AppPublicKey`, the branch is a literal empty block — the response `big_y` and `big_c` are never validated against anything before being serialised and delivered to the user via `resolve_yields_for`.

The structural parallel to the Linea finding is exact: just as `_finalizeBlocks` dropped the `blobShnarfExists[finalShnarf] != 0` guard and proceeded with an unverified computed value, `respond_ckd` omits the validity guard for the `AppPublicKey` variant and proceeds to deliver an unverified response.

The `CKDRequest` key (including `app_public_key`, `app_id`, `domain_id`) is observable on-chain the moment `request_app_private_key` is called. A Byzantine participant can reconstruct the exact map key, craft any `CKDResponse{big_y, big_c}` they choose, and call `respond_ckd`. Because `assert_caller_is_attested_participant_and_protocol_active` only requires the caller to be an attested participant — not that a threshold of participants agreed — a single Byzantine node suffices.

---

### Impact Explanation

The user's private-key recovery from a CKD response depends entirely on `big_y` and `big_c`. If the attacker picks any scalar `r` and sets:

```
big_c = r · G1
big_y = r · app_public_key   (= r·a·G1, where a is the user's secret scalar)
```

the user recovers `r` as their derived private key. The attacker chose `r`, so they know the user's "secret" key. This constitutes **confidential key derivation output without the required participant authorization** — a Critical impact under the allowed scope. The user has no on-chain signal that the response was not produced by the threshold MPC protocol.

---

### Likelihood Explanation

- The attacker must be a single attested participant in the MPC network — strictly below the signing threshold.
- The `CKDRequest` key is written to contract storage and is publicly readable.
- No threshold cooperation, TEE break, or key leak is required.
- The only race condition is whether the legitimate MPC response arrives first; a Byzantine participant can front-run by submitting immediately after observing the pending request.

---

### Recommendation

Add an equivalent on-chain validity check for the `AppPublicKey` variant, or — if on-chain verification is cryptographically impossible without `pk2` — reject `AppPublicKey` requests at the contract level and require all CKD callers to use `AppPublicKeyPV`, which provides the pairing-based guarantee. At minimum, document that `AppPublicKey` requests carry no Byzantine-resistance guarantee and that users must verify the response off-chain before using the derived key.

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {
        // Either add an equivalent check here, or panic to force AppPublicKeyPV usage
        env::panic_str("AppPublicKey variant is not supported: use AppPublicKeyPV");
    }
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
```

---

### Proof of Concept

1. User calls `request_app_private_key` with `AppPublicKey(a·G1)` on a CKD domain. The `CKDRequest` is stored in `pending_ckd_requests`.

2. Byzantine participant observes the pending request on-chain, picks scalar `r`, and calls:
   ```
   respond_ckd(
     request = <the stored CKDRequest>,
     response = CKDResponse { big_y: r·(a·G1), big_c: r·G1 }
   )
   ```

3. `respond_ckd` reaches the `AppPublicKey(_) => {}` branch — no check is performed. [1](#0-0) 

4. `resolve_yields_for` drains the pending queue and delivers the attacker-crafted `CKDResponse` to the user. [2](#0-1) 

5. The user receives `big_c = r·G1` and `big_y = r·a·G1`. They compute their private key as `r` — which the attacker already knows.

6. The `AppPublicKeyPV` path, by contrast, would have enforced `ckd_output_check` and rejected any response not satisfying the pairing equation. [3](#0-2)

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
