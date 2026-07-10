### Title
Single Byzantine Attested Participant Can Forge `AppPublicKey` CKD Response, Bypassing Threshold Requirement — (`crates/contract/src/lib.rs`)

---

### Summary

`respond_ckd()` performs no cryptographic output check for the `AppPublicKey` variant of `CKDAppPublicKey`. Any single attested participant can call it with arbitrary `(big_c, big_y)` G1 points for a pending `AppPublicKey` CKD request, causing the contract to immediately drain all queued yields with the forged response. The threshold requirement — that the correct output requires cooperation of at least `t` participants — is entirely unenforced for this variant at the contract level.

---

### Finding Description

In `respond_ckd()`, the match on `request.app_public_key` has an asymmetric guard:

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

For `AppPublicKeyPV`, `ckd_output_check` verifies the pairing equation `e(big_c, g2) = e(big_y, app_pk2) · e(hash_point, public_key)`, which cryptographically binds the response to the threshold-held `msk`. For `AppPublicKey`, the arm is a no-op. Any single attested participant can supply arbitrary `(big_c, big_y)` bytes and the contract will accept them.

`resolve_yields_for` then immediately drains every queued yield for that request key with the attacker-supplied bytes:

```rust
pending_requests::resolve_yields_for(
    &mut self.pending_ckd_requests,
    &request,
    serde_json::to_vec(&response).unwrap(),
)
``` [2](#0-1) 

`resolve_yields_for` removes the map entry and resumes all queued yields in one pass — there is no multi-party accumulation or threshold counter: [3](#0-2) 

The existing unit test already proves this path: it submits `big_y = [1u8;48]`, `big_c = [2u8;48]` (neither is a valid G1 point) from a single attested participant and asserts the call succeeds and the pending request is cleared: [4](#0-3) 

The design requirement in the protocol specification explicitly states: *"No single node in the MPC network should be capable of computing s."* [5](#0-4) 

For `AppPublicKeyPV`, the on-chain pairing check enforces this property. For `AppPublicKey`, nothing does.

---

### Impact Explanation

The victim's `unmask` operation computes `s = big_c − a · big_y` where `a` is the victim's private scalar: [6](#0-5) 

An attacker who sets `big_y = 0` (the G1 point at infinity) and `big_c = r·G1` for a known scalar `r` causes the victim to compute `s = r·G1 − a·0 = r·G1`. The attacker knows `r` and therefore knows `s` — the victim's confidential key. Any system that uses this key (e.g., BLS signing, TEE-bound secrets) is compromised: the attacker can sign on behalf of the victim.

Even without that specific construction, the attacker can:
1. Deliver any forged `s` to the victim without the victim being able to detect it at the contract level.
2. Permanently clear the pending request, preventing the victim from ever receiving the correct threshold-computed output (the yield is consumed and cannot be re-queued).

This constitutes unauthorized confidential key derivation output without the required threshold participant authorization, matching the Critical impact scope: *"Bypass of threshold-signature requirements or unauthorized access to MPC key shares, signing capability, or secret material that materially enables forgery or secret recovery."*

---

### Likelihood Explanation

The attacker must be a single attested MPC participant — Byzantine behavior strictly below the signing threshold, which is explicitly within scope. The attack requires no collusion, no TEE physical compromise, and no leaked keys. The attacker only needs to observe a pending `AppPublicKey` CKD request (visible on-chain via `get_pending_ckd_request`) and race the legitimate coordinator's `respond_ckd` call. The existing unit test confirms the path is reachable with zero cryptographic effort.

---

### Recommendation

Apply the same pairing-based output check to `AppPublicKey` requests. Since `AppPublicKey` only provides a G1 point `pk1` (without a paired G2 point), the current `ckd_output_check` cannot be applied directly. The fix is to require callers to use `AppPublicKeyPV` (which carries both `pk1` and `pk2`) so the on-chain check is always possible, or to introduce a threshold-accumulation mechanism at the contract level (requiring `t` distinct attested participants to submit matching responses before any yield is resolved).

---

### Proof of Concept

The existing test `respond_ckd__should_succeed_when_response_is_valid_and_request_exists` already constitutes a proof of concept: [4](#0-3) 

It submits a valid `AppPublicKey` CKD request, then calls `respond_ckd()` with `big_y = [1u8;48]` and `big_c = [2u8;48]` from a single attested participant, and asserts the call succeeds and the pending request is cleared — confirming no cryptographic binding check exists for this variant. To demonstrate key-control, replace the response with `big_y = G1_IDENTITY` (the compressed point-at-infinity encoding) and `big_c = r·G1` for a known `r`; the victim's `unmask(a)` will return `r·G1`, a key the attacker fully controls.

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

**File:** crates/contract/src/lib.rs (L684-688)
```rust
        pending_requests::resolve_yields_for(
            &mut self.pending_ckd_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
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

**File:** crates/contract/src/pending_requests.rs (L66-88)
```rust
pub(crate) fn resolve_yields_for<K>(
    requests: &mut LookupMap<K, Vec<YieldIndex>>,
    request: &K,
    response_bytes: Vec<u8>,
) -> Result<(), Error>
where
    K: BorshSerialize + BorshDeserialize + Clone + Ord,
{
    let resumed = requests
        .remove(request)
        .unwrap_or_default()
        .into_iter()
        .map(|YieldIndex { data_id }| {
            env::promise_yield_resume(&data_id, response_bytes.clone());
        })
        .count();

    if resumed > 0 {
        Ok(())
    } else {
        Err(InvalidParameters::RequestNotFound.into())
    }
}
```

**File:** crates/threshold-signatures/docs/confidential_key_derivation/confidential-key-derivation.md (L113-115)
```markdown
  known by *app*
- No single node in the *MPC network* should be capable of computing $`s`$. This
avoids key leakage in the case a single TEE is compromised
```

**File:** crates/threshold-signatures/src/confidential_key_derivation.rs (L51-55)
```rust
    /// Takes a secret scalar and returns
    /// s <- C − a ⋅ Y = msk ⋅ H ( `app_id` )
    pub fn unmask(&self, secret_scalar: Scalar) -> Signature {
        self.big_c - self.big_y * secret_scalar
    }
```
