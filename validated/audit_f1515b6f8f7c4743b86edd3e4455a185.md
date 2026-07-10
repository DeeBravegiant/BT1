### Title
`respond_ckd` Skips Cryptographic Output Verification for `AppPublicKey` Variant, Allowing a Single Byzantine Node to Forge CKD Responses — (`File: crates/contract/src/lib.rs`)

---

### Summary

`respond_ckd` enforces a pairing-based output check (`ckd_output_check`) only for the `AppPublicKeyPV` variant of a CKD request. For the `AppPublicKey` variant, the check is silently skipped. This is the direct analog of the reported `VotesUpgradeable::delegate` bypass: one code path enforces the critical invariant; the other path — reachable by any single attested participant — bypasses it entirely. A single Byzantine node below the signing threshold can submit an arbitrary `CKDResponse` for any pending `AppPublicKey` request, and the contract will accept and resolve it without any cryptographic proof that the threshold protocol was actually executed.

---

### Finding Description

In `respond_ckd`, after the caller is authenticated as an attested participant, the contract branches on the request's `app_public_key` variant:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← no check at all
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For `AppPublicKeyPV`, `ckd_output_check` verifies the pairing equation `e(big_c, g2) = e(big_y, app_pk2) · e(hash_point, public_key)`, which cryptographically proves the response was produced using the MPC master key and the correct threshold protocol. [2](#0-1) 

For `AppPublicKey`, the arm is an empty no-op. The response fields `big_y` and `big_c` are accepted unconditionally. The existing unit test for this path explicitly uses `[1u8; 48]` and `[2u8; 48]` — bytes that are not valid BLS12-381 curve points — and the test expects success, confirming that no point validation or protocol-output check is performed. [3](#0-2) 

The `AppPublicKey` variant is the non-publicly-verifiable form: the user supplies only a G1 point `pk1 = g1·s`, keeping their scalar `s` private. Because `pk2 = g2·s` is absent, the on-chain pairing check cannot be computed. However, the contract makes no attempt to substitute any alternative verification; it simply resolves the pending yield with whatever bytes the responding node supplied.

---

### Impact Explanation

Any single attested participant — one node, strictly below the signing threshold — can:

1. Observe a pending `AppPublicKey` CKD request in `pending_ckd_requests`.
2. Call `respond_ckd` with an arbitrary `CKDResponse` (any `big_y`, `big_c` bytes).
3. The contract passes the `assert_caller_is_attested_participant_and_protocol_active` check, skips the empty `AppPublicKey` arm, and calls `resolve_yields_for`, permanently resolving the request with the forged data. [4](#0-3) 

The user's pending CKD request is consumed and they receive attacker-controlled key material. The threshold MPC protocol — which requires cooperation of `t` nodes — is bypassed entirely for this request class. This satisfies the Critical impact criterion: *unauthorized confidential key derivation output without the required participant authorization / bypass of threshold-signature requirements*.

---

### Likelihood Explanation

The attacker needs only to be a single attested participant in the current epoch. No collusion, no key leakage, and no privileged operator access is required. The attack is a direct on-chain call to a public contract method. Any participant who turns Byzantine after attestation can execute it immediately against any queued `AppPublicKey` CKD request.

---

### Recommendation

Add an equivalent output check for the `AppPublicKey` variant. Because `pk2` is absent, the existing pairing equation cannot be used directly. The recommended fix is one of:

1. **Require `AppPublicKeyPV` for all on-chain-resolved CKD requests.** Deprecate `AppPublicKey` for the `request_app_private_key` / `respond_ckd` flow, since on-chain resolution without verifiability is inherently unsafe.
2. **Introduce a threshold-vote mechanism for `AppPublicKey` responses**, analogous to `vote_pk` / `vote_reshared`, so that the contract only resolves the request once `t` participants have submitted the same `(big_y, big_c)` pair.
3. **At minimum, add a guard** that rejects `respond_ckd` calls for `AppPublicKey` requests unless the response matches a quorum-agreed value stored during the MPC round.

The fix should mirror the pattern already correct for `AppPublicKeyPV`:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {
        // Either reject, or enforce a threshold-vote before resolving.
        env::panic_str("AppPublicKey variant requires threshold-vote resolution");
    }
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
```

---

### Proof of Concept

1. Deploy the contract and reach `Running` state with participants A (honest ×(t−1)) and B (Byzantine ×1).
2. User calls `request_app_private_key` with `CKDAppPublicKey::AppPublicKey(pk1)`.
3. Before honest nodes respond, B calls:
   ```
   respond_ckd(ckd_request, CKDResponse { big_y: [0u8;48], big_c: [0u8;48] })
   ```
4. The contract executes lines 675–688: the `AppPublicKey` arm is a no-op, `resolve_yields_for` fires, and the user's yield resumes with the forged `(big_y, big_c)`.
5. The user's off-chain verification (`big_c - big_y·s ≠ hash_point·msk`) fails, confirming the response is forged — but the on-chain request has already been permanently consumed. [5](#0-4) [6](#0-5)

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

**File:** crates/contract/src/lib.rs (L3404-3441)
```rust
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

**File:** crates/contract/src/primitives/ckd.rs (L8-31)
```rust
#[derive(Debug, Clone, Eq, Ord, PartialEq, PartialOrd)]
#[near(serializers=[borsh, json])]
pub struct CKDRequest {
    /// The app ephemeral public key
    pub app_public_key: dtos::CKDAppPublicKey,
    pub app_id: dtos::CkdAppId,
    pub domain_id: DomainId,
}

impl CKDRequest {
    pub fn new(
        app_public_key: dtos::CKDAppPublicKey,
        domain_id: DomainId,
        predecessor_id: &AccountId,
        derivation_path: &str,
    ) -> Self {
        let app_id = derive_app_id(predecessor_id, derivation_path);
        Self {
            app_public_key,
            app_id,
            domain_id,
        }
    }
}
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
