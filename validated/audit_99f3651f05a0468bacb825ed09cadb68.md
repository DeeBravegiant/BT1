### Title
Missing CKD Output Validation for `AppPublicKey` Variant Allows Single Byzantine Participant to Deliver Attacker-Controlled Key Material — (File: `crates/contract/src/lib.rs`)

### Summary
`respond_ckd` performs a cryptographic pairing check (`ckd_output_check`) on the CKD response only when the request uses the `AppPublicKeyPV` variant. For the legacy `AppPublicKey` variant — the default format — no output check is performed. A single Byzantine attested participant can therefore submit an arbitrary `CKDResponse`, delivering key material whose discrete log they know to the requesting user, bypassing the threshold-security guarantee of the CKD protocol.

### Finding Description
In `respond_ckd` (`crates/contract/src/lib.rs`, lines 675–682):

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← no check
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
```

The `ckd_output_check` function (`crates/contract/src/primitives/ckd.rs`, lines 80–102) verifies the pairing equation `e(big_c, g2) = e(big_y, app_pk2) · e(hash_point, public_key)`, which proves the response encodes a correctly-formed ElGamal encryption of the BLS signature under the user's app public key. This check is **only applied to `AppPublicKeyPV`**. For `AppPublicKey` (the legacy single-G1-point format, which is the default and is used in production e2e tests), the contract unconditionally accepts any `(big_y, big_c)` pair.

The analog to the ERC20 report is exact: one code path (`AppPublicKeyPV`) validates the external response; the other (`AppPublicKey`) silently accepts it without validation, just as `SafeERC20` validates the return value while the raw call does not.

### Impact Explanation
The CKD protocol's security guarantee is that no single participant learns the derived key. The `ckd_output_check` is the on-chain enforcement of this guarantee. Without it for `AppPublicKey` requests, a single Byzantine attested participant can:

1. Wait for a legitimate `request_app_private_key` call using `AppPublicKey(pk1)` where `pk1 = app_sk · G1`.
2. Call `respond_ckd` with a crafted response: `big_y = r·G1`, `big_c = r·pk1 + x·G1` for an attacker-chosen scalar `x`.
3. The contract accepts this without verification and resumes the yield, delivering `(big_y, big_c)` to the user.
4. The user computes `big_c − app_sk · big_y = r·pk1 + x·G1 − app_sk·r·G1 = x·G1`, recovering `x·G1` as their "private key."
5. The attacker knows `x`, so they possess the user's derived secret.

The user believes they hold a unique confidential key; in reality the attacker controls it. This enables the attacker to decrypt any data the user encrypts to that key, or to forge signatures the user makes with it — a direct secret-material compromise.

**Impact: High** — participant/attestation authorization bypass that delivers attacker-controlled secret material, breaking the threshold-security invariant of the CKD protocol for all `AppPublicKey` requests.

### Likelihood Explanation
- `AppPublicKey` is the legacy default format, documented and used in production e2e tests (`crates/node/src/tests.rs`, line 376).
- Only one Byzantine attested participant is required — no threshold collusion.
- The attacker needs only to observe a pending `AppPublicKey` CKD request (visible on-chain) and race the honest nodes' `respond_ckd` submission.
- The `respond_ckd` function does not deduplicate responses; the first accepted response wins.

**Likelihood: Medium** — requires a single Byzantine TEE-attested participant and a pending `AppPublicKey` request.

### Recommendation
Apply `ckd_output_check` to both variants. For `AppPublicKey`, the check can be performed using only `pk1` (constructing a degenerate `AppPublicKeyPV` with `pk2 = identity` is not appropriate; instead, verify the BLS signature directly against the master public key using the G1 hash point). Alternatively, deprecate `AppPublicKey` in favour of `AppPublicKeyPV` and reject `AppPublicKey` requests in `respond_ckd` until a suitable check is defined.

At minimum, add a guard:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {
        // TODO: add output check equivalent for legacy format, or reject
        env::panic_str("AppPublicKey variant is not supported in respond_ckd without output check");
    }
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
```

### Proof of Concept

**Root cause location:** [1](#0-0) 

**`ckd_output_check` that is skipped for `AppPublicKey`:** [2](#0-1) 

**`AppPublicKey` is the legacy default, used in production:** [3](#0-2) 

**`AppPublicKeyPV` receives the check; `AppPublicKey` does not — asymmetry confirmed:** [4](#0-3)

### Citations

**File:** crates/contract/src/lib.rs (L675-689)
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
