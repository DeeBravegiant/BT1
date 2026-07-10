### Title
Unverified CKD Response for Legacy `AppPublicKey` Variant Allows Byzantine Coordinator to Force Arbitrary Key Derivation - (File: crates/contract/src/lib.rs)

### Summary
`respond_ckd` applies cryptographic output verification only for the `AppPublicKeyPV` (publicly verifiable) variant. For the legacy `AppPublicKey` variant, the response branch is empty — no on-chain pairing check is performed. A single Byzantine attested participant acting as coordinator can submit an arbitrary `CKDResponse`, causing the user to derive an attacker-controlled key and enabling theft of funds from the user's derived foreign-chain wallet.

### Finding Description
In `respond_ckd` (`crates/contract/src/lib.rs:653-689`), after retrieving the BLS12-381 network public key, the contract branches on the request's `app_public_key` variant:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← no check
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
``` [1](#0-0) 

For `AppPublicKeyPV`, `ckd_output_check` (`crates/contract/src/

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
