### Title
Unvalidated CKD Response for `AppPublicKey` Variant Allows Single Malicious Participant to Corrupt Key Derivation Output - (File: `crates/contract/src/lib.rs`)

---

### Summary

The `respond_ckd` function in the MPC contract applies an on-chain pairing check (`ckd_output_check`) to validate CKD responses for the `AppPublicKeyPV` (publicly verifiable) variant, but performs **no validation whatsoever** for the `AppPublicKey` (legacy, privately verifiable) variant. A single malicious attested participant — strictly below the signing threshold — can call `respond_ckd` with an arbitrary `CKDResponse` for any pending `AppPublicKey` CKD request. The contract accepts and resolves the yield, returning fabricated output to the requesting user. This bypasses the threshold correctness guarantee for the CKD flow.

---

### Finding Description

In `respond_ckd`, the validation branch is asymmetric:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}   // ← no validation at all
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For `AppPublicKeyPV`, `ckd_output_check` verifies the pairing equation `e(C, G2) = e(Y, pk2) · e(H(pk, app_id), pk)` on-chain, ensuring the encrypted output is cryptographically bound to the MPC master key and the user's app public key. [2](#0-1) 

For `AppPublicKey`, there is no analogous check. The contract proceeds directly to `resolve_yields_for`, which resolves the pending yield and delivers whatever `big_y` and `big_c` the caller supplied. [3](#0-2) 

The only gate on `respond_ckd` is `assert_caller_is_attested_participant_and_protocol_active`, which requires the caller to be **any** single attested participant — not a threshold quorum. [4](#0-3) 

The `AppPublicKey` variant is explicitly documented as the "privately verifiable" (legacy) path, where verification is expected to happen off-chain after the app decrypts the output. However, the absence of any on-chain guard means the contract cannot distinguish a legitimately computed CKD output from a fabricated one. [5](#0-4) 

The analog to M-01 is direct: just as the `Collateral` contract accepted any `product` address without checking factory origin — allowing a fake product to return arbitrary maintenance values — `respond_ckd` accepts any `CKDResponse` for `AppPublicKey` requests without checking cryptographic correctness, allowing a single malicious participant to return arbitrary `(big_y, big_c)` values.

---

### Impact Explanation

A single malicious attested participant (strictly below the signing threshold) can:

1. Monitor the chain for any pending `AppPublicKey` CKD request.
2. Race the legitimate leader by calling `respond_ckd` with fabricated `big_y` and `big_c` values.
3. The contract accepts the fake response, resolves the yield, and delivers the fabricated output to the requesting user.

The user receives `(big_y', big_c')` chosen by the attacker. When the user computes `sig = big_c' − a · big_y'`, they obtain a value that is not `msk · H(pk, app_id)` — the correct BLS signature underlying their derived key. The CKD request is consumed (the pending entry is removed), so the user must pay and resubmit. If the user does not independently verify the output before use, they may derive and use an incorrect key for their application (e.g., encrypting data, signing foreign-chain transactions), leading to data loss or application malfunction.

This breaks the production safety invariant that CKD output correctness requires a threshold of honest participants: for `AppPublicKey` requests, one dishonest participant suffices to corrupt the output.

**Matched impact**: Medium — request-lifecycle and participant-state manipulation that breaks production safety/accounting invariants without requiring network-level DoS or operator misconfiguration.

---

### Likelihood Explanation

- Requires one malicious attested participant, which is strictly below the threshold and is the minimal Byzantine assumption the system is supposed to tolerate.
- Pending CKD requests are publicly visible on-chain; no privileged information is needed to identify targets.
- The attacker only needs to submit a transaction before the legitimate leader's `respond_ckd` call lands — a realistic race on a live network.
- The `AppPublicKey` variant is the legacy path still in active use (the SDK and README both document it as the default single-point format). [6](#0-5) 

---

### Recommendation

1. **Deprecate `AppPublicKey` in favor of `AppPublicKeyPV`**: The publicly verifiable variant already has a correct on-chain check. Migrate all callers to `AppPublicKeyPV` and reject new `AppPublicKey` requests.

2. **If `AppPublicKey` must be retained**: Add a best-effort structural validity check on the G1 point (on-curve, prime-order subgroup) at request submission time in `request_app_private_key`, and document clearly that the `AppPublicKey` variant provides no on-chain response integrity guarantee and is vulnerable to a single malicious participant.

3. **Require threshold quorum for `respond_ckd`**: Restructure the response flow so that `respond_ckd` is only callable after a threshold of participants have submitted matching responses off-chain (similar to how the signing protocol aggregates shares before submitting on-chain), eliminating the single-participant attack surface entirely.

---

### Proof of Concept

```
1. Alice calls request_app_private_key({
       domain_id: <CKD domain>,
       derivation_path: "my-key",
       app_public_key: AppPublicKey(<alice_g1_point>)   // legacy variant
   }) with 1 yoctoNEAR deposit.
   → Contract stores CKDRequest{app_id=H(alice, "my-key"), app_public_key=AppPublicKey(...)} in pending_ckd_requests.

2. Mallory (a single attested participant) observes the pending request on-chain.

3. Mallory calls respond_ckd(
       request = CKDRequest{app_id=H(alice, "my-key"), ...},
       response = CKDResponse{ big_y: [0u8;48], big_c: [0u8;48] }  // arbitrary garbage
   ).
   → assert_caller_is_attested_participant_and_protocol_active() passes (Mallory is attested).
   → AppPublicKey branch: no validation executed.
   → resolve_yields_for resolves Alice's yield with the fake (big_y, big_c).

4. Alice's contract callback receives (big_y=0, big_c=0).
   Alice computes sig = big_c - a·big_y = 0 - a·0 = 0 (identity point).
   Verification e(H(pk,app_id), pk) = e(0, G2) fails → Alice detects corruption only if she checks.
   Alice's CKD request is consumed; she must resubmit and pay again.
   If Alice skips verification and uses sig=0 as her derived key, her application is broken.
```

The root cause — the empty `AppPublicKey(_) => {}` arm in `respond_ckd` — is at: [7](#0-6) 

compared to the validated `AppPublicKeyPV` arm: [8](#0-7)

### Citations

**File:** crates/contract/src/lib.rs (L484-491)
```rust
        match &request.app_public_key {
            dtos::CKDAppPublicKey::AppPublicKey(_) => {}
            dtos::CKDAppPublicKey::AppPublicKeyPV(pk) => {
                if !app_public_key_check(pk) {
                    env::panic_str("app public key check failed")
                }
            }
        }
```

**File:** crates/contract/src/lib.rs (L653-667)
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

**File:** crates/contract/README.md (L118-121)
```markdown
- `app_public_key`: the ephemeral public key for the CKD request. Two formats are supported:
  - **Privately verifiable** (legacy): a single G1 point, e.g. `"bls12381g1:<base58>"` or `{"AppPublicKey": "bls12381g1:<base58>"}`.
  - **Publicly verifiable**: a pair of points `(pk1, pk2) = (a·G1, a·G2)`, passed as `{"AppPublicKeyPV": {"pk1": "bls12381g1:<base58>", "pk2": "bls12381g2:<base58>"}}`. This allows anyone to verify the encrypted result on-chain without the app's secret key.
- `domain_id` (integer): identifies the master key to use for deriving the ckd, and must correspond to bls12381.
```
