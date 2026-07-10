### Title
Single Byzantine MPC Node Can Inject Arbitrary CKD Output for `AppPublicKey` Requests — (`File: crates/contract/src/lib.rs`)

### Summary
`respond_ckd` performs no cryptographic verification of the `CKDResponse` when the request uses the `AppPublicKey` (privately-verifiable) variant. A single attested-but-Byzantine MPC participant can race honest nodes by immediately submitting an arbitrary fake response, which the contract accepts and delivers to the user as the authoritative confidential key derivation output.

### Finding Description
In `respond_ckd`, the contract branches on the request's `app_public_key` variant:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← no check at all
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
```

For `AppPublicKeyPV`, the contract runs `ckd_output_check` — a BLS12-381 pairing check that verifies `e(big_c, g2) = e(big_y, app_pk2) · e(hash_point, public_key)`. This check is cryptographically binding: a fake response cannot pass it without knowledge of the master secret.

For `AppPublicKey`, the arm is empty. Any `CKDResponse` — with arbitrary `big_y` and `big_c` — is immediately forwarded to `resolve_yields_for`, which drains the pending queue and delivers the response to every waiting caller.

The `respond_ckd` entry point requires only `assert_caller_is_attested_participant_and_protocol_active()` — a single attested participant suffices. No threshold agreement is required to call this function.

### Impact Explanation
A single Byzantine MPC node (strictly below the signing threshold) can deliver an arbitrary `CKDResponse` for any in-flight `AppPublicKey` CKD request. The user receives a fake `(big_y, big_c)` pair. Because the `AppPublicKey` variant is privately verifiable, the user cannot detect the forgery without their own private scalar. The derived "confidential key" they compute from the fake output is meaningless and unrelated to the MPC master secret. This constitutes unauthorized confidential key derivation output delivered without the required participant authorization.

**Impact class:** Critical — confidential key derivation output without required participant authorization.

### Likelihood Explanation
The attack is straightforward and requires no cryptographic capability beyond being an attested participant:

1. The Byzantine node monitors the NEAR chain for `request_app_private_key` calls using `AppPublicKey`.
2. It immediately calls `respond_ckd` with arbitrary `big_y`/`big_c` values — no protocol computation needed.
3. Honest nodes must run the actual multi-round CKD protocol before they can respond; the Byzantine node has a structural timing advantage.
4. Once `resolve_yields_for` drains the queue, honest nodes receive `RequestNotFound` and cannot correct the outcome.

The `AppPublicKey` variant is the legacy default and is actively used in production (confirmed by the e2e test `ckd_response__passes_cryptographic_verification`).

### Recommendation
Either:
- **Deprecate `AppPublicKey` in `respond_ckd`** and require all new requests to use `AppPublicKeyPV`, which is publicly verifiable on-chain; or
- **Require threshold-many distinct attested participants to submit matching responses** before `resolve_yields_for` is called, analogous to how threshold signatures require agreement before a signature is accepted.

The `AppPublicKeyPV` path already demonstrates the correct pattern: `ckd_output_check` enforces the pairing equation before any state mutation occurs.

### Proof of Concept

1. User calls `request_app_private_key` with `AppPublicKey(pk1)` → entry added to `pending_ckd_requests`.
2. Byzantine attested node calls `respond_ckd(request, CKDResponse { big_y: [1u8;48], big_c: [2u8;48] })` — the same dummy values used in the unit test at line 4513.
3. Contract executes the `AppPublicKey(_) => {}` arm — no check — then calls `resolve_yields_for`, draining the queue.
4. User's yield resolves with the fake `(big_y, big_c)`. Honest nodes subsequently receive `Err(RequestNotFound)`.
5. User computes `big_c − private_scalar · big_y` and obtains a point unrelated to the MPC master secret.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** crates/contract/src/lib.rs (L684-689)
```rust
        pending_requests::resolve_yields_for(
            &mut self.pending_ckd_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
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
