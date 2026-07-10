### Title
Unverified CKD Response for `AppPublicKey` Variant Allows Single Byzantine Participant to Forge Confidential Key Derivation Output - (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_ckd` performs cryptographic output verification only for the `AppPublicKeyPV` variant of `CKDAppPublicKey`. For the `AppPublicKey` (legacy) variant, the response is accepted and the pending yield is resolved with **zero on-chain verification**. A single Byzantine attested participant can call `respond_ckd` with an arbitrary `CKDResponse`, and the contract will deliver that forged key material to the waiting user.

---

### Finding Description

In `respond_ckd`, after confirming the caller is an attested participant, the contract branches on the request's `app_public_key` variant:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← no check at all
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

For `AppPublicKeyPV`, `ckd_output_check` verifies the BLS12-381 pairing equation `e(big_c, g2) = e(big_y, app_pk2) · e(hash_point, public_key)`, which cryptographically binds the response to the network's master secret key and the user's app identity. [2](#0-1) 

For `AppPublicKey`, the empty arm `{}` means **any** `CKDResponse` — including one with completely arbitrary `big_c` and `big_y` bytes — is immediately forwarded to `resolve_yields_for`, which resumes the user's yield with the forged data. [3](#0-2) 

The `AppPublicKey` variant is the legacy/default format: a plain G1 key submitted as a bare string. It is the format produced by the example CLI without `--publicly-verifiable` and is what all pre-PV integrations use. [4](#0-3) 

The existing test `test_respond_ckd_fails_for_attested_non_participant` inadvertently confirms the issue: it calls `respond_ckd` with `big_y = [1u8; 48]` and `big_c = [2u8; 48]` (not valid BLS12-381 points) for an `AppPublicKey` request and asserts the call **succeeds**: [5](#0-4) 

---

### Impact Explanation

A single Byzantine attested participant (strictly below the signing threshold) can:

1. Observe any pending `AppPublicKey` CKD request in the contract's `pending_ckd_requests` map.
2. Construct an arbitrary `CKDResponse{big_y, big_c}` — e.g., the identity point, a random point, or a point that encodes a known secret.
3. Call `respond_ckd(request, forged_response)` from their attested account.
4. The contract resolves the yield and delivers the forged key material to the user.

The user's application then derives a private key from `big_c` and `big_y` that was chosen by the attacker, not by the threshold protocol. This constitutes **unauthorized confidential key derivation output without the required participant authorization** — the threshold protocol was never run, yet the contract records and delivers a result as if it was.

This maps directly to the allowed critical impact: *"Unauthorized transaction execution, threshold signature issuance, or confidential key derivation output without the required participant authorization."*

---

### Likelihood Explanation

- `AppPublicKey` is the default, legacy variant used by all integrations that do not opt into the publicly-verifiable flow. It is the format shown in the contract README's primary example and in the example CLI without flags.
- Any single attested participant — one node out of the full set — can trigger this. No collusion is required.
- The attacker-controlled entry path is direct: call `respond_ckd` with a crafted response for any observed pending request.

---

### Recommendation

Apply `ckd_output_check` for both variants. For `AppPublicKey`, the G1 key is available as `pk1`; construct a synthetic `CKDAppPublicKeyPV` with `pk2` derived from the same scalar, or require callers to always supply `AppPublicKeyPV`. Alternatively, reject `AppPublicKey` requests at the `request_app_private_key` entry point and require all new requests to use `AppPublicKeyPV`, which enables on-chain verification.

---

### Proof of Concept

1. Alice submits `request_app_private_key` with `AppPublicKey(some_g1_point)`.
2. Mallory (a single Byzantine attested participant) observes the pending request.
3. Mallory calls:
   ```
   respond_ckd(
     request = <Alice's CKDRequest>,
     response = CKDResponse { big_y: [0u8;48], big_c: [0u8;48] }
   )
   ```
4. The contract's `respond_ckd` hits the `AppPublicKey(_) => {}` arm — no check — and calls `resolve_yields_for`, resuming Alice's yield with the forged response.
5. Alice's callback receives `CKDResponse { big_y: 0, big_c: 0 }` and derives a private key from attacker-controlled material.
6. The threshold MPC protocol was never executed; Mallory acted alone. [1](#0-0) [6](#0-5)

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

**File:** crates/contract/src/lib.rs (L4513-4521)
```rust
        let valid_response = CKDResponse {
            big_y: dtos::Bls12381G1PublicKey([1u8; 48]),
            big_c: dtos::Bls12381G1PublicKey([2u8; 48]),
        };

        // This should succeed (attested participant)
        contract
            .respond_ckd(ckd_request.clone(), valid_response.clone())
            .expect("Participant should be allowed to respond_ckd");
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

**File:** crates/near-mpc-crypto-types/src/ckd.rs (L15-18)
```rust
pub enum CKDAppPublicKey {
    AppPublicKey(Bls12381G1PublicKey),
    AppPublicKeyPV(CKDAppPublicKeyPV),
}
```
