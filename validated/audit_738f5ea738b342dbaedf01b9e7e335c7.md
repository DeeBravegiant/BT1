### Title
Single Attested Participant Can Inject Unverified CKD Response for `AppPublicKey` Requests, Permanently Blocking Legitimate Threshold Output — (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_ckd` in the MPC smart contract performs cryptographic output verification **only** for the `AppPublicKeyPV` variant of CKD requests. For the `AppPublicKey` (legacy) variant, the match arm is an empty no-op. Any single attested participant can call `respond_ckd` with an arbitrary fake `CKDResponse`, the contract accepts it unconditionally, resolves the pending yield, and delivers the corrupted key to the user. The legitimate threshold-computed response is permanently blocked because `resolve_yields_for` consumes the request on first call.

---

### Finding Description

In `respond_ckd`, after confirming the caller is an attested participant, the contract branches on the request's `app_public_key` variant:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← no verification
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

For `AppPublicKeyPV`, `ckd_output_check` performs a BLS12-381 pairing check that verifies `e(big_c, G2) = e(big_y, pk2) · e(hash_point, mpc_pk)`, cryptographically binding the response to the MPC network's public key and the user's app identity. [2](#0-1) 

For `AppPublicKey`, the arm is empty. The contract proceeds directly to `resolve_yields_for`, which serializes whatever `CKDResponse` was passed and resumes the yield, delivering it to the caller. No check is made that `big_y` or `big_c` are valid BLS12-381 points, let alone that they encode the correct threshold-computed output.

The existing unit test confirms this: it passes `big_y = [1u8; 48]` and `big_c = [2u8; 48]` (not valid curve points) and the call succeeds. [3](#0-2) 

The `AppPublicKey` variant is described as "privately verifiable, legacy" — the user is expected to verify correctness off-chain after decryption. However, the contract still resolves and discards the pending request on the first `respond_ckd` call regardless of response validity, making off-chain detection useless for recovering the correct key. [4](#0-3) 

---

### Impact Explanation

**Vulnerability class:** Unauthorized confidential key derivation output without required threshold participant authorization.

A single Byzantine attested participant can:

1. Monitor the NEAR blockchain for any pending `AppPublicKey` CKD request.
2. Immediately call `respond_ckd` with a fabricated `CKDResponse` (`big_y`, `big_c` set to arbitrary bytes).
3. The contract accepts it, calls `resolve_yields_for`, and delivers the fake encrypted key to the requesting contract/user.
4. The legitimate threshold-computed response — produced by the honest MPC network after completing the multi-round BLS12-381 CKD protocol — arrives too late; the request has already been consumed.

The user receives a key that does not decrypt to any meaningful secret. If the derived key controls funds (e.g., a TEE app wallet key derived via CKD), those funds are permanently inaccessible. The malicious participant can repeat this attack on every subsequent re-submission of the same request, making recovery impossible as long as the attacker remains active.

This matches the allowed critical impact: **"confidential key derivation output without the required participant authorization"** — the output is produced by one participant acting alone, bypassing the threshold computation entirely.

---

### Likelihood Explanation

- Requires only **one** Byzantine attested participant (strictly below the signing threshold).
- The attacker needs no special knowledge: the pending request is visible on-chain.
- Front-running is straightforward: the MPC threshold computation takes multiple network rounds, while the attacker's `respond_ckd` call is a single NEAR transaction.
- The `AppPublicKey` variant is the legacy path and is still fully supported and callable by users. [5](#0-4) 

---

### Recommendation

**Short-term:** Reject `AppPublicKey` CKD requests at the contract level (or at `respond_ckd`) until a verification path exists. The `AppPublicKeyPV` variant already provides on-chain verifiable output and should be the only accepted variant for new requests.

**Long-term:** If `AppPublicKey` must remain supported, the contract should require the responding participant to include a BLS12-381 proof of correct encryption (e.g., a DLEQ proof over G1 that `big_c = a · big_y` for the user's secret `a`), or require the MPC nodes to co-sign the response before it is accepted.

The asymmetry between the two variants is the root cause:

```rust
// AppPublicKeyPV — protected
dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
    if !ckd_output_check(...) { env::panic_str("CKD output check failed"); }
}
// AppPublicKey — unprotected
dtos::CKDAppPublicKey::AppPublicKey(_) => {}
``` [6](#0-5) 

---

### Proof of Concept

```rust
// Attacker is an attested participant. User has submitted:
//   request_app_private_key({ app_public_key: AppPublicKey(honest_pk), ... })
// Attacker front-runs the honest MPC response:

let fake_response = CKDResponse {
    big_y: dtos::Bls12381G1PublicKey([0xAA; 48]),  // garbage bytes
    big_c: dtos::Bls12381G1PublicKey([0xBB; 48]),  // garbage bytes
};

// Called by the attacker (an attested participant) before the MPC network responds:
contract.respond_ckd(victim_ckd_request, fake_response)
    .expect("accepted with no verification");

// The pending request is now gone. The user's yield resumes with the fake key.
// The honest MPC response, when it arrives, finds no pending request and is silently dropped.
// The user's app decrypts garbage and cannot obtain the real derived key.
```

This is directly confirmed by the existing test `respond_ckd__should_succeed_when_response_is_valid_and_request_exists`, which passes `[1u8; 48]` / `[2u8; 48]` as the response and expects success — demonstrating that no cryptographic validity is required. [3](#0-2)

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

**File:** crates/contract/src/lib.rs (L3403-3441)
```rust
    #[test]
    fn respond_ckd__should_succeed_when_response_is_valid_and_request_exists() {
        let (context, mut contract, _secret_key) = basic_setup(Curve::Bls12381, &mut OsRng);
        let app_public_key: dtos::Bls12381G1PublicKey =
            "bls12381g1:6KtVVcAAGacrjNGePN8bp3KV6fYGrw1rFsyc7cVJCqR16Zc2ZFg3HX3hSZxSfv1oH6"
                .parse()
                .unwrap();
        let request = CKDRequestArgs {
            derivation_path: "".to_string(),
            app_public_key: CKDAppPublicKey::AppPublicKey(app_public_key.clone()),
            domain_id: dtos::DomainId::default(),
        };
        let ckd_request = CKDRequest::new(
            CKDAppPublicKey::AppPublicKey(app_public_key),
            request.domain_id,
            &context.predecessor_account_id,
            &request.derivation_path,
        );
        contract.request_app_private_key(request);
        contract.get_pending_ckd_request(&ckd_request).unwrap();

        let response = CKDResponse {
            big_y: dtos::Bls12381G1PublicKey([1u8; 48]),
            big_c: dtos::Bls12381G1PublicKey([2u8; 48]),
        };

        with_active_participant_and_attested_context(&contract);

        match contract.respond_ckd(ckd_request.clone(), response.clone()) {
            Ok(_) => {
                contract
                    .return_ck_and_clean_state_on_success(ckd_request.clone(), Ok(response))
                    .detach();

                assert!(contract.get_pending_ckd_request(&ckd_request).is_none(),);
            }
            Err(_) => panic!("respond_ckd should not fail"),
        }
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
