### Title
Single Byzantine MPC Node Can Deliver Forged CKD Response for `AppPublicKey` Variant, Bypassing Threshold Authorization — (File: `crates/contract/src/lib.rs`)

---

### Summary

The `respond_ckd` function in the MPC contract performs no cryptographic verification of the CKD response when the request uses the `AppPublicKey` variant. A single attested MPC participant (a Byzantine node strictly below the signing threshold) can submit an arbitrary `CKDResponse` for any pending `AppPublicKey` CKD request. The contract unconditionally drains the entire yield queue for that request via `resolve_yields_for`, delivering the forged confidential key to every waiting caller — bypassing the threshold authorization requirement that is the core security guarantee of the MPC network.

---

### Finding Description

In `respond_ckd`, the contract branches on the request's `app_public_key` variant to decide whether to verify the response: [1](#0-0) 

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← NO VERIFICATION
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
```

For `AppPublicKeyPV`, the contract

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
