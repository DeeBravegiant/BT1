### Title
Single Attested Participant Can Inject Arbitrary CKD Response for `AppPublicKey` Requests, Bypassing Threshold Authorization - (File: crates/contract/src/lib.rs)

### Summary

In `respond_ckd`, when the pending request uses the `CKDAppPublicKey::AppPublicKey` (legacy/privately-verifiable) variant, the contract performs **zero** cryptographic verification of the submitted `CKDResponse`. A single attested MPC participant — strictly below the signing threshold — can call `respond_ckd` with arbitrary `big_y` and `big_c` values and the contract will accept and deliver the forged response to the user. The user then derives a "private key" that the attacker fully controls, bypassing the threshold requirement entirely.

### Finding Description

`respond_ckd` in `crates/contract/src/lib.rs` contains a match on `request.app_public_key`:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← NO VERIFICATION
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For the `AppPublicKeyPV` variant, `ckd_output_check` enforces the BLS pairing equation `e(big_c, g2) = e(big_y, app_pk2) · e(hash_point, public_key)`, which cryptographically proves the response was computed using the MPC master key. [2](#0-1) 

For the `AppPublicKey` (legacy) variant, the arm is `{}` — the contract does nothing. Any `big_y` and `big_c` values pass through unconditionally and are delivered to the user via `resolve_yields_for`. [3](#0-2) 

The `AppPublicKey` variant is the default legacy format, still fully supported and documented as the primary example in the contract README. [4](#0-3) 

The only gate before the match is `assert_caller_is_attested_participant_and_protocol_active()`, which requires the caller to be a single attested participant — not a threshold of them. [5](#0-4) 

### Impact Explanation

The CKD protocol for `AppPublicKey` works as follows: the MPC network computes `big_c = H(pk, app_id) · msk + app_pk1 · y` and `big_y = y · G1`. The user recovers the derived key as `big_c − app_secret_key · big_y = H(pk, app_id) · msk`. [6](#0-5) 

An attacker who is a single attested participant can submit `big_y = G1_identity` (the identity point, a valid G1 element) and `big_c = t · G1` for any scalar `t` they choose. The user then computes `t · G1 − app_secret_key · 0 = t · G1`, and derives a private key whose discrete log is `t` — a value the attacker chose and knows. The attacker has thus fully determined the user's derived private key without the threshold of participants ever participating.

This is **unauthorized confidential key derivation output without the required participant authorization**, matching the Critical impact tier.

### Likelihood Explanation

The attacker must be a single attested MPC participant. Attestation requires a valid TEE quote, but once a node is attested and in the participant set, it can call `respond_ckd` unilaterally for any pending `AppPublicKey` request. No collusion with other participants is needed. The `AppPublicKey` variant is the legacy default and is actively used by existing integrations. The attack requires no special timing, no network-level access, and no privileged operator key — only a compromised or malicious attested node.

### Recommendation

Apply the same on-chain cryptographic verification to `AppPublicKey` responses that is already applied to `AppPublicKeyPV` responses. Since the `AppPublicKey` variant does not include a G2 component (`pk2`), the pairing-based `ckd_output_check` cannot be applied directly. The recommended mitigations are:

1. **Deprecate and remove** the `AppPublicKey` (privately-verifiable) variant from `respond_ckd`. Require all new CKD requests to use `AppPublicKeyPV`, which supports on-chain verification.
2. If backward compatibility must be maintained, add a contract-level note that `AppPublicKey` responses are **not verified on-chain** and that the security guarantee for this variant relies entirely on the off-chain MPC protocol and TEE integrity — and document this centralized trust assumption explicitly in user-facing documentation, analogous to the recommendation in the referenced external report.

### Proof of Concept

1. User Alice calls `request_app_private_key` with `app_public_key = "bls12381g1:<alice_pk1>"` (the `AppPublicKey` legacy variant) and `derivation_path = "mykey"`.
2. The contract stores the pending CKD request and parks Alice's call via yield-resume.
3. Attacker Bob, a single attested MPC participant, calls `respond_ckd` with:
   - `request` = the same `CKDRequest` (reconstructed from on-chain state)
   - `response.big_y` = the compressed encoding of the G1 identity point
   - `response.big_c` = `t · G1` for any scalar `t` Bob chooses
4. The contract's `respond_ckd` reaches the `AppPublicKey(_) => {}` arm, skips all verification, and calls `resolve_yields_for`, delivering Bob's forged response to Alice.
5. Alice computes her derived key as `big_c − alice_secret · big_y = t · G1 − alice_secret · 0 = t · G1`. Her derived private key is `t`, which Bob chose and knows.
6. Bob now knows Alice's derived private key for `"mykey"` without any threshold of MPC participants having participated in the computation. [7](#0-6) [8](#0-7)

### Citations

**File:** crates/contract/src/lib.rs (L653-689)
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

**File:** crates/contract/src/primitives/ckd.rs (L497-530)
```rust
    /// Builds a CKD output that satisfies
    /// `e(big_c, g2) = e(big_y, app_pk2) . e(hash_point, public_key)`.
    fn make_valid_ckd_output(
        rng: &mut StdRng,
    ) -> (
        dtos::CkdAppId,
        CKDResponse,
        dtos::CKDAppPublicKeyPV,
        dtos::Bls12381G2PublicKey,
    ) {
        let msk = Scalar::random(&mut *rng);
        let network_pk =
            dtos::Bls12381G2PublicKey((G2Projective::generator() * msk).to_compressed());

        let app_scalar = Scalar::random(&mut *rng);
        let app_pk1 = G1Projective::generator() * app_scalar;
        let app_pk = make_app_public_key_pv(app_scalar);

        let app_id = derive_app_id(&"alice.near".parse().unwrap(), "path");
        let hash_point = G1Projective::hash_to_curve(
            &[network_pk.0.as_slice(), app_id.as_ref()].concat(),
            NEAR_CKD_DOMAIN,
            &[],
        );

        let y = Scalar::random(&mut *rng);
        let big_y = G1Projective::generator() * y;
        let big_c = hash_point * msk + app_pk1 * y;
        let response = CKDResponse {
            big_y: dtos::Bls12381G1PublicKey(big_y.to_compressed()),
            big_c: dtos::Bls12381G1PublicKey(big_c.to_compressed()),
        };
        (app_id, response, app_pk, network_pk)
    }
```

**File:** crates/contract/README.md (L119-120)
```markdown
  - **Privately verifiable** (legacy): a single G1 point, e.g. `"bls12381g1:<base58>"` or `{"AppPublicKey": "bls12381g1:<base58>"}`.
  - **Publicly verifiable**: a pair of points `(pk1, pk2) = (a·G1, a·G2)`, passed as `{"AppPublicKeyPV": {"pk1": "bls12381g1:<base58>", "pk2": "bls12381g2:<base58>"}}`. This allows anyone to verify the encrypted result on-chain without the app's secret key.
```
