### Title
Missing On-Chain Cryptographic Verification of CKD Response for `AppPublicKey` Variant Enables Single Byzantine Participant to Corrupt Confidential Key Derivation Output - (File: `crates/contract/src/lib.rs`)

### Summary

The `respond_ckd` function in the MPC contract performs no cryptographic verification of the CKD response when the request uses the `AppPublicKey` (privately verifiable) variant. A single Byzantine attested participant — strictly below the signing threshold — can submit any arbitrary `(big_y, big_c)` pair and the contract will accept and deliver it to the user, breaking the threshold-security guarantee for CKD.

### Finding Description

In `crates/contract/src/lib.rs` at lines 675–682, `respond_ckd` branches on the app public key variant:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}   // ← zero verification
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For the `AppPublicKeyPV` variant, `ckd_output_check` enforces the pairing equation:

> `e(big_c, G₂) = e(big_y, app_pk₂) · e(H(pk ‖ app_id), pk)` [2](#0-1) 

This equation is also enforced inside the MPC node's own coordinator check (`aggregated_output_check`) for the publicly verifiable protocol path: [3](#0-2) 

For the `AppPublicKey` variant, **neither** check is applied. Any attested participant can call `respond_ckd` with arbitrary bytes for `big_y` and `big_c`, and the contract resolves the pending yield immediately, delivering the forged payload to the waiting user.

The `AppPublicKey` variant is still actively supported as the "legacy" path: [4](#0-3) 

The analogy to the ENS report is direct: the ENS `validateSignature` used the wrong coordinate-conversion formula but produced correct results only because a redundant conversion chain forced `Z = 1`. Here, the missing pairing check "works" only because honest nodes produce correct outputs; a single Byzantine node exploits the absent check to deliver a forged result — exactly the scenario that materialises when the compensating assumption (all nodes honest) is violated.

### Impact Explanation

A single Byzantine attested participant (below the signing threshold) can:

1. Monitor the NEAR chain for pending `AppPublicKey` CKD requests.
2. Race the honest coordinator by calling `respond_ckd` with arbitrary `(big_y, big_c)` — e.g., setting `big_y = G₁` (generator) and `big_c = G₁` — before the legitimate MPC round completes.
3. The contract resolves the yield and delivers the forged pair to the user.
4. The user computes `sig = big_c − big_y · app_sk`, which is not a valid BLS signature for the correct `app_id`; the client-side `verify_signature` call fails.
5. The legitimate response from the honest coordinator arrives after the yield is already consumed and is silently discarded.

The attacker cannot produce a *valid-but-wrong* confidential key (they do not know the MPC master secret `msk`), but they can permanently invalidate any in-flight `AppPublicKey` CKD request, forcing the user to resubmit. Repeated racing constitutes a persistent, targeted denial of the CKD service for specific users, breaking the request-lifecycle safety invariant that only a threshold-quorum of participants can determine the outcome of a CKD request.

This maps to the allowed impact: **Medium — request-lifecycle manipulation that breaks production safety/accounting invariants without relying on network-level DoS or operator misconfiguration.**

### Likelihood Explanation

- Requires only **one** Byzantine attested participant (below threshold).
- The attack is purely on-chain: monitor mempool/chain for `request_app_private_key` events, then submit `respond_ckd` with a higher gas price before the honest coordinator's transaction lands.
- No cryptographic material is needed; the forged `(big_y, big_c)` can be any valid compressed G1 points.
- The `AppPublicKey` variant is the legacy default path and is widely used.

### Recommendation

1. **Short-term**: In `respond_ckd`, add a basic validity check for the `AppPublicKey` variant — at minimum verify that `big_y` and `big_c` are valid, non-identity G1 points using `bls12381_p1_decompress` (which aborts on malformed input). This does not prove correctness but raises the bar.
2. **Medium-term**: Deprecate the `AppPublicKey` variant and require `AppPublicKeyPV` for all new requests. The publicly verifiable variant already has a complete on-chain pairing check that enforces threshold correctness without revealing the app secret key.
3. **Long-term**: Consider requiring that `respond_ckd` can only be called by the designated coordinator for a given request, or require a threshold of participant signatures on the response, mirroring the threshold guarantee of the signing protocol.

### Proof of Concept

```
1. Alice submits request_app_private_key with AppPublicKey variant (legacy path).
   → Contract stores pending yield for Alice's CKD request.

2. Byzantine participant Eve (single attested node, below threshold) observes
   the pending request on-chain.

3. Eve calls respond_ckd(ckd_request, CKDResponse { big_y: [1u8;48], big_c: [2u8;48] })
   with a higher gas price than the honest coordinator's pending transaction.

4. Contract executes respond_ckd:
   - assert_caller_is_attested_participant_and_protocol_active() → passes (Eve is attested)
   - match AppPublicKey(_) => {}  ← no verification
   - resolve_yields_for(...) → yield resolved, forged payload delivered to Alice

5. Honest coordinator's respond_ckd transaction lands but finds no pending yield
   → silently discarded.

6. Alice receives (big_y=[1u8;48], big_c=[2u8;48]).
   Alice computes sig = big_c − big_y * app_sk → garbage G1 point.
   verify_signature(mpc_vk, app_id, sig) → Err(InvalidSignature).
   Alice must resubmit; Eve can repeat indefinitely.
``` [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** crates/threshold-signatures/src/confidential_key_derivation/protocol_pv.rs (L221-236)
```rust
/// Check that `e(big_c, g2) = e(big_y, app_pk2) . e(hash_point, public_key)`
fn aggregated_output_check(
    output: &CKDOutput,
    app_pk: &PublicVerificationKey,
    public_key: &VerifyingKey,
    hash_point: &ElementG1,
) -> bool {
    if !check_valid_point_g1(output.big_c.into()) || !check_valid_point_g1(output.big_y.into()) {
        return false;
    }
    multi_miller_loop(&[
        (output.big_c, -ElementG2::generator()),
        (output.big_y, app_pk.pk2),
        (*hash_point, public_key.to_element()),
    ])
}
```

**File:** crates/contract/README.md (L119-120)
```markdown
  - **Privately verifiable** (legacy): a single G1 point, e.g. `"bls12381g1:<base58>"` or `{"AppPublicKey": "bls12381g1:<base58>"}`.
  - **Publicly verifiable**: a pair of points `(pk1, pk2) = (a·G1, a·G2)`, passed as `{"AppPublicKeyPV": {"pk1": "bls12381g1:<base58>", "pk2": "bls12381g2:<base58>"}}`. This allows anyone to verify the encrypted result on-chain without the app's secret key.
```

**File:** crates/threshold-signatures/src/confidential_key_derivation/ciphersuite.rs (L229-255)
```rust
pub fn verify_signature(
    verifying_key: &VerifyingKey,
    msg: &[u8],
    signature: &Signature,
) -> Result<(), frost_core::Error<BLS12381SHA256>> {
    let element1: G1Affine = signature.into();
    if !check_valid_point_g1(element1) || element1.is_identity().into() {
        return Err(frost_core::Error::InvalidSignature);
    }
    let element2: G2Affine = verifying_key.to_element().into();
    if !check_valid_point_g2(element2) || element2.is_identity().into() {
        return Err(frost_core::Error::MalformedVerifyingKey);
    }

    // Concatenate the master public key (96 bytes) in the hash computation
    // H(pk || app_id) when H is a random oracle
    let base1 = hash_app_id_with_pk(verifying_key, msg).into();
    let base2 =
        <<BLS12381SHA256 as frost_core::Ciphersuite>::Group as frost_core::Group>::generator()
            .into();

    if blstrs::pairing(&base1, &element2).eq(&blstrs::pairing(&element1, &base2)) {
        Ok(())
    } else {
        Err(frost_core::Error::InvalidSignature)
    }
}
```
