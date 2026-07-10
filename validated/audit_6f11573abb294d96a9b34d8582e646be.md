### Title
Missing CKD Output Verification for `AppPublicKey` Variant Allows Byzantine Participant to Deliver Arbitrary Key Material — (`crates/contract/src/lib.rs`)

---

### Summary

The `respond_ckd` entry point performs cryptographic output verification **only** for the `AppPublicKeyPV` variant. The `AppPublicKey` (non-PV) match arm is an unconditional no-op. A single Byzantine attested participant can call `respond_ckd` with any `(big_y, big_c)` pair for a pending `AppPublicKey` CKD request, and the contract will accept and deliver the fabricated blob to the user without any pairing check.

---

### Finding Description

In `respond_ckd`, after confirming the caller is an attested participant, the contract branches on `request.app_public_key`:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← no-op, zero verification
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For `AppPublicKeyPV`, `ckd_output_check` enforces the pairing equation `e(big_c, g2) = e(big_y, app_pk2) · e(H(pk‖app_id), pk_network)` using the BLS12-381 host functions: [2](#0-1) 

For `AppPublicKey`, **no equivalent check exists**. The unverified `response` is immediately serialised and delivered via `resolve_yields_for`: [3](#0-2) 

**Why the contract cannot self-verify `AppPublicKey` responses:** The pairing check requires a G2 counterpart (`app_pk2`) of the user's ephemeral key. `AppPublicKey` carries only a G1 point; without the G2 key the contract cannot evaluate `e(big_y, app_pk2)`. The `AppPublicKeyPV` struct was introduced precisely to supply both components and enable on-chain verification: [4](#0-3) [5](#0-4) 

The `AppPublicKey` variant remains a live, production-reachable code path accepted by `CKDRequestArgs` and handled by the node: [6](#0-5) 

---

### Impact Explanation

A single Byzantine attested participant (strictly below the signing threshold) can:

1. Wait for any pending `AppPublicKey` CKD request in contract state.
2. Call `respond_ckd` with an arbitrary `CKDResponse { big_y: [1u8;48], big_c: [2u8;48] }`.
3. The contract skips all verification and resolves the yield with the fabricated blob.
4. The requesting user receives attacker-controlled key material instead of the honest MPC output `msk·H(pk‖app_id)`.

The user's derived key is wrong or attacker-influenced. Any secret encrypted to that derived key is either unrecoverable or recoverable by the attacker (if the attacker chose `(big_y, big_c)` such that they know the corresponding scalar). This constitutes **unauthorized confidential key derivation output** delivered without the required threshold participant authorization.

---

### Likelihood Explanation

- `AppPublicKey` is the default/legacy CKD variant and is actively used in production flows.
- Only **one** attested participant needs to act maliciously; no threshold collusion is required.
- The attacker does not need to compromise the TEE binary — they only need to call the NEAR contract method directly from the participant account, which is always possible for any registered attested participant.
- The contract's only guard is `assert_caller_is_attested_participant_and_protocol_active()`, which any legitimate (but malicious) node passes. [7](#0-6) 

---

### Recommendation

**Short-term:** Deprecate and reject `CKDAppPublicKey::AppPublicKey` requests at the contract level, requiring all callers to use `AppPublicKeyPV` so that `ckd_output_check` is always enforced.

**Long-term / alternative:** Implement a G1-only verification path. The equivalent check without a G2 app key is `e(big_c, g2) = e(big_y, g2)^{app_sk} · e(H(pk‖app_id), pk_network)`, which cannot be evaluated on-chain without `app_sk`. Therefore the only sound on-chain fix is to mandate `AppPublicKeyPV`.

---

### Proof of Concept

```rust
// 1. User submits a CKD request with AppPublicKey variant
let request = CKDRequest::new(
    CKDAppPublicKey::AppPublicKey(Bls12381G1PublicKey([1u8; 48])),
    domain_id,
    &"alice.near".parse().unwrap(),
    "my/path",
);
contract.request_ckd(request.clone()); // pending yield created

// 2. Byzantine attested participant submits fabricated response
let fake_response = CKDResponse {
    big_y: Bls12381G1PublicKey([1u8; 48]),
    big_c: Bls12381G1PublicKey([2u8; 48]),
};
let result = contract.respond_ckd(request, fake_response);

// 3. Contract returns Ok(()) — no pairing check was performed
assert!(result.is_ok());

// 4. User's callback receives the fabricated blob, not msk·H(pk‖app_id)
// Verification: e(big_c - a·big_y, g2) ≠ e(H(pk‖app_id), pk_network)
```

The empty arm at line 676 means step 3 always succeeds regardless of the cryptographic content of `fake_response`, confirming the invariant is broken for all `AppPublicKey` requests.

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

**File:** crates/near-mpc-crypto-types/src/ckd.rs (L15-18)
```rust
pub enum CKDAppPublicKey {
    AppPublicKey(Bls12381G1PublicKey),
    AppPublicKeyPV(CKDAppPublicKeyPV),
}
```

**File:** crates/near-mpc-crypto-types/src/ckd.rs (L71-74)
```rust
pub struct CKDAppPublicKeyPV {
    pub pk1: Bls12381G1PublicKey,
    pub pk2: Bls12381G2PublicKey,
}
```

**File:** crates/near-mpc-contract-interface/src/types/ckd.rs (L12-16)
```rust
pub struct CKDRequestArgs {
    pub derivation_path: String,
    pub app_public_key: CKDAppPublicKey,
    pub domain_id: DomainId,
}
```
