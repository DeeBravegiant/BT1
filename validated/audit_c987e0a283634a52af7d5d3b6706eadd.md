### Title
Missing CKD Output Verification for Legacy `AppPublicKey` Variant in `respond_ckd` — (File: `crates/contract/src/lib.rs`)

### Summary

`respond_ckd` performs a cryptographic pairing check (`ckd_output_check`) on the response only when the request uses the `AppPublicKeyPV` (publicly verifiable) variant. When the legacy `AppPublicKey` variant is used, the response arm is an empty no-op, allowing any single attested participant acting as coordinator to submit an arbitrary `CKDResponse` that the contract accepts unconditionally.

### Finding Description

In `respond_ckd`, the match on `request.app_public_key` is asymmetric:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}   // ← no check
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For `AppPublicKeyPV`, `ckd_output_check` verifies the BLS12-381 pairing equation `e(big_c, g2) = e(big_y, app_pk2) · e(hash_point, public_key)`, which cryptographically proves the response encodes a correctly derived key. [2](#0-1) 

For `AppPublicKey` (the legacy, privately verifiable mode), the arm is empty — the contract performs **zero verification** on `big_c` and `big_y` before resolving the pending yield with the response. [3](#0-2) 

The `AppPublicKey` variant is the documented legacy path and is actively used in production (the README shows it as the default single-G1-point format). [4](#0-3) 

### Impact Explanation

A single malicious attested participant acting as the CKD coordinator can call `respond_ckd` with an arbitrary `CKDResponse{big_c, big_y}` for any pending `AppPublicKey` request. The contract resolves the yield and delivers the attacker-controlled response to the user. The user's application receives incorrect key material — the decrypted value `big_c − a·big_y` (where `a` is the user's private scalar) is not the legitimate derived key `hash_point · msk`. This breaks the core confidential-key-derivation safety invariant: the contract guarantees that the response is a valid encryption of the MPC-derived key, but for the legacy variant it enforces nothing. Impact: **Medium** — request-lifecycle and contract execution-flow manipulation that breaks a production safety invariant without requiring threshold collusion.

### Likelihood Explanation

`respond_ckd` requires only that the caller be an attested participant (`assert_caller_is_attested_participant_and_protocol_active`). [5](#0-4)  A single compromised or malicious node below the signing threshold can call this function. The `AppPublicKey` path is the legacy default and is exercised in production. The attacker does not need to forge a threshold signature — the CKD response is submitted unilaterally by the coordinator.

### Recommendation

Apply the same `ckd_output_check` guard to the `AppPublicKey` arm. Because `AppPublicKey` carries only `pk1` (a G1 point, no `pk2`), the existing `ckd_output_check` (which requires `pk2`) cannot be used directly. The fix requires either:

1. Deriving `pk2` from `pk1` at submission time (requires the user to supply a G2 companion, i.e., migrating to `AppPublicKeyPV`), or
2. Implementing a separate, weaker on-chain check for the legacy path — for example, verifying that `big_c` and `big_y` are valid G1 points and that `big_y ≠ identity` — and documenting that full output correctness for `AppPublicKey` remains off-chain.

The cleanest fix is to deprecate `AppPublicKey` and require all new requests to use `AppPublicKeyPV`, which already has the full pairing check on both submission (`app_public_key_check`) and response (`ckd_output_check`). [6](#0-5) 

### Proof of Concept

1. User submits `request_app_private_key` with `AppPublicKey(pk1)` for domain `d`.
2. The contract stores the pending CKD request and issues a yield.
3. A malicious attested participant calls:
   ```json
   respond_ckd(
     request = { app_public_key: {AppPublicKey: pk1}, app_id: ..., domain_id: d },
     response = { big_c: <attacker_point>, big_y: <identity> }
   )
   ```
4. The `AppPublicKey(_) => {}` arm executes — no check runs.
5. `resolve_yields_for` resolves the yield with the attacker-supplied response.
6. The user's promise callback receives `{big_c: attacker_point, big_y: identity}` and computes `attacker_point − a·identity = attacker_point` as the "derived key" — entirely attacker-controlled, not the legitimate `hash_point · msk`. [7](#0-6) [8](#0-7)

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

**File:** crates/contract/src/lib.rs (L666-666)
```rust
        self.assert_caller_is_attested_participant_and_protocol_active();
```

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

**File:** crates/contract/README.md (L118-121)
```markdown
- `app_public_key`: the ephemeral public key for the CKD request. Two formats are supported:
  - **Privately verifiable** (legacy): a single G1 point, e.g. `"bls12381g1:<base58>"` or `{"AppPublicKey": "bls12381g1:<base58>"}`.
  - **Publicly verifiable**: a pair of points `(pk1, pk2) = (a·G1, a·G2)`, passed as `{"AppPublicKeyPV": {"pk1": "bls12381g1:<base58>", "pk2": "bls12381g2:<base58>"}}`. This allows anyone to verify the encrypted result on-chain without the app's secret key.
- `domain_id` (integer): identifies the master key to use for deriving the ckd, and must correspond to bls12381.
```
