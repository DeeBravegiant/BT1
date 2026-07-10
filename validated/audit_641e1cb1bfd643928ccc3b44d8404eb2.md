### Title
Missing Cryptographic Output Verification for `AppPublicKey` CKD Variant Allows Byzantine Participant to Forge Confidential Key Derivation Output — (`crates/contract/src/lib.rs`)

### Summary

The `respond_ckd` entry point in the NEAR MPC contract performs a cryptographic pairing check on the CKD response only for the `AppPublicKeyPV` variant. For the `AppPublicKey` (non-PV) variant, the match arm is empty — no check is performed. A single Byzantine attested participant can therefore call `respond_ckd` with an arbitrary `(big_y, big_c)` pair and the contract will accept it unconditionally, resolving the pending CKD yield with attacker-chosen output.

### Finding Description

In `crates/contract/src/lib.rs`, `respond_ckd` dispatches on the request's `app_public_key` variant:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← no check
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For `AppPublicKeyPV`, `ckd_output_check` enforces the pairing equation `e(big_c, g2) = e(big_y, app_pk2) · e(H(pk, app_id), msk_pk)`, binding the response to the correct `app_id` and the network master secret key. [2](#0-1) 

For `AppPublicKey`, no such equation is checked. After passing only the attestation guard (`assert_caller_is_attested_participant_and_protocol_active`), the response is immediately forwarded to `resolve_yields_for`, which resolves the pending NEAR yield with whatever `(big_y, big_c)` the caller supplied. [3](#0-2) 

The `AppPublicKey` variant is a first-class production enum member, not feature-gated or test-only: [4](#0-3) 

### Impact Explanation

The decrypted confidential key a victim app receives is `C - a·Y`, where `C = big_c` and `Y = big_y`. If the attacker supplies `big_y = G1` and `big_c = G1`, the app receives `G1 - a·G1` — a value the attacker can predict or control. More generally, the attacker can choose any `(big_y, big_c)` pair for which they know the discrete log relationship, effectively selecting the confidential key the victim app derives. Any assets or secrets the app protects with that key are then under the attacker's control. This constitutes **unauthorized confidential key derivation output** — a Critical impact under the allowed scope.

### Likelihood Explanation

The attacker must be a single attested participant — below the signing threshold. Attestation is a meaningful barrier, but the threat model explicitly includes Byzantine behavior from individual participants. No threshold collusion is required; a single malicious node races to call `respond_ckd` before honest nodes complete the protocol. Because NEAR's yield/resume mechanism accepts the first valid response, the race is winnable by a Byzantine participant who monitors the mempool for `AppPublicKey`-variant CKD requests.

### Recommendation

Apply `ckd_output_check` to the `AppPublicKey` variant as well. Because `AppPublicKey` carries only a G1 key (no G2 component), the check must be adapted: either require callers to supply a G2 counterpart (making it equivalent to `AppPublicKeyPV`), or derive the hash point and verify `e(big_c, g2) = e(big_y + H(pk, app_id), msk_pk)` using only the network public key and the G1 app key. Alternatively, remove the `AppPublicKey` variant from production paths and require all callers to use `AppPublicKeyPV`.

### Proof of Concept

A contract unit test demonstrating the issue:

1. Submit a `CKDRequest` with `app_public_key: AppPublicKey(some_g1_pk)` and a victim `app_id`.
2. Call `respond_ckd` as an attested participant with `big_y = G1_generator` and `big_c = G1_generator`.
3. Assert the call succeeds (no panic, no error).
4. Compute the key the app would derive: `big_c - a * big_y` where `a` is the app's secret scalar.
5. Assert it does **not** equal `msk * H(pk, app_id)` — confirming the contract accepted a forged, attacker-chosen output.

The generator constants needed for step 2 are already present in the codebase: [5](#0-4)

### Citations

**File:** crates/contract/src/lib.rs (L654-688)
```rust
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

        let PublicKeyExtended::Bls12381 {
            public_key: dtos::PublicKey::Bls12381(public_key),
        } = self.public_key_extended(request.domain_id)?
        else {
            env::panic_str("Domain is not compatible with CKD (expected Bls12381 curve)");
        };

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

**File:** crates/contract/src/primitives/ckd.rs (L33-40)
```rust
/// Uncompressed encoding of the G1 generator.
const G1_GENERATOR_UNCOMPRESSED: [u8; 96] = [
    23, 241, 211, 167, 49, 151, 215, 148, 38, 149, 99, 140, 79, 169, 172, 15, 195, 104, 140, 79,
    151, 116, 185, 5, 161, 78, 58, 63, 23, 27, 172, 88, 108, 85, 232, 63, 249, 122, 26, 239, 251,
    58, 240, 10, 219, 34, 198, 187, 8, 179, 244, 129, 227, 170, 160, 241, 160, 158, 48, 237, 116,
    29, 138, 228, 252, 245, 224, 149, 213, 208, 10, 246, 0, 219, 24, 203, 44, 4, 179, 237, 208, 60,
    199, 68, 162, 136, 138, 228, 12, 170, 35, 41, 70, 197, 231, 225,
];
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
