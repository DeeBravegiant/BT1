Looking at the relevant code carefully before rendering a verdict.

### Title
Missing CKD Output Verification for `AppPublicKey` Variant Allows Single Attested Participant to Forge Key Derivation Output — (`crates/contract/src/lib.rs`)

---

### Summary

The `respond_ckd` function in the chain-signature contract performs cryptographic verification of the CKD response **only** for the `AppPublicKeyPV` variant. For the `AppPublicKey` (non-PV) variant, the response is accepted unconditionally after a single attested participant submits it. A single Byzantine attested participant can therefore call `respond_ckd` with arbitrary `big_y` and `big_c` values, causing the contract to resolve the pending yield with a forged CKD output that is not bound to the MPC network secret key.

---

### Finding Description

The `respond_ckd` entry point at `crates/contract/src/lib.rs:654` enforces three guards before processing a response:

1. `assert_caller_is_signer()` — caller must be a registered signer account
2. `is_running_or_resharing()` — protocol state check
3. `assert_caller_is_attested_participant_and_protocol_active()` — caller must be TEE-attested

After those guards, the function branches on the request variant:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← no verification
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For `AppPublicKeyPV`, `ckd_output_check` verifies the pairing equation `e(big_c, g2) = e(big_y, app_pk2) · e(H(pk‖app_id), msk_pk)`, which cryptographically binds the response to the MPC network secret key and the caller's `app_id`. [2](#0-1) 

For `AppPublicKey`, the empty arm `{}` means **no such check is performed**. The function immediately proceeds to `resolve_yields_for`, which resumes every queued yield with the attacker-supplied bytes: [3](#0-2) 

`resolve_yields_for` calls `env::promise_yield_resume` for every queued yield with the raw serialized response, delivering the forged `(big_y, big_c)` to all waiting callers. [4](#0-3) 

The `AppPublicKey` variant is a fully supported production path — it is accepted by `CKDRequestArgs`, handled by the node's CKD provider, and used in e2e tests. [5](#0-4) 

The `AppPublicKey` variant carries only a G1 public key (no G2 component), so the pairing-based `ckd_output_check` cannot be applied directly. However, the contract makes no attempt at any alternative binding check, and `respond_ckd` resolves the request after a **single** participant responds — there is no on-chain threshold aggregation for CKD responses. [6](#0-5) 

---

### Impact Explanation

A single Byzantine attested participant can:

1. Observe a pending `CKDRequest` with `AppPublicKey` variant on-chain.
2. Call `respond_ckd(request, CKDResponse { big_y: arbitrary_g1, big_c: arbitrary_g1 })`.
3. The contract accepts the call, resolves the yield, and delivers the forged `(big_y, big_c)` to the requesting application.

The attacker chose `big_y` and `big_c`, so they know the relationship between them. The output is not bound to `msk * H(pk‖app_id)`. The requesting application receives a CKD output that appears legitimate (same type, same structure) but is entirely under the attacker's control. This constitutes **unauthorized confidential key derivation output delivered without the required MPC computation**, matching the Critical impact tier.

---

### Likelihood Explanation

The attacker must be a single TEE-attested MPC participant — Byzantine behavior strictly below the signing threshold. Attestation is a prerequisite but does not prevent a participant from submitting a malformed response for the unverified variant. The `AppPublicKey` variant is the legacy/default path (it is the deserialization fallback for plain G1 keys), making it the more commonly used variant in practice. [7](#0-6) 

---

### Recommendation

Apply the same binding check to `AppPublicKey` responses. Since `AppPublicKey` lacks a G2 component, the existing pairing equation cannot be used directly. Two options:

1. **Deprecate `AppPublicKey` in favour of `AppPublicKeyPV`** and reject `AppPublicKey` requests at the contract level, forcing all callers to supply the G2 component needed for on-chain verification.
2. **Require threshold aggregation on-chain for `AppPublicKey`**: collect responses from `t` distinct attested participants and only resolve the yield when they agree, rather than accepting the first response.

Option 1 is simpler and eliminates the unverifiable code path entirely.

---

### Proof of Concept

```rust
// Pseudocode unit test outline
let mut contract = setup_running_contract_with_attested_participant();

// Caller submits a CKD request with the non-PV variant
let ckd_request = CKDRequest {
    app_public_key: CKDAppPublicKey::AppPublicKey(Bls12381G1PublicKey([0xAB; 48])),
    app_id: derive_app_id(&"alice.near".parse().unwrap(), "path"),
    domain_id: DomainId(0),
};
contract.request_ckd(CKDRequestArgs { ... });

// Attacker (single attested participant) submits forged response
let forged_response = CKDResponse {
    big_y: Bls12381G1PublicKey([1u8; 48]),
    big_c: Bls12381G1PublicKey([2u8; 48]),
};

// Contract accepts without any cryptographic check
let result = contract.respond_ckd(ckd_request, forged_response);
assert!(result.is_ok()); // passes — no verification for AppPublicKey variant

// The yield is resolved with attacker-chosen bytes; caller receives forged output
// big_c ≠ msk·H(pk‖app_id) + big_y·a — the equation is not satisfied
```

The empty arm at `crates/contract/src/lib.rs:676` is the root cause: `dtos::CKDAppPublicKey::AppPublicKey(_) => {}` performs no check before `resolve_yields_for` is called. [8](#0-7)

### Citations

**File:** crates/contract/src/lib.rs (L675-689)
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

**File:** crates/near-mpc-contract-interface/src/types/ckd.rs (L12-16)
```rust
pub struct CKDRequestArgs {
    pub derivation_path: String,
    pub app_public_key: CKDAppPublicKey,
    pub domain_id: DomainId,
}
```

**File:** crates/near-mpc-crypto-types/src/ckd.rs (L15-18)
```rust
pub enum CKDAppPublicKey {
    AppPublicKey(Bls12381G1PublicKey),
    AppPublicKeyPV(CKDAppPublicKeyPV),
}
```

**File:** crates/near-mpc-crypto-types/src/ckd.rs (L40-52)
```rust
        #[derive(Deserialize)]
        #[serde(untagged)]
        enum Helper {
            Tagged(Tagged),
            Plain(Bls12381G1PublicKey),
        }

        match Helper::deserialize(deserializer)? {
            Helper::Tagged(Tagged::AppPublicKey(pk)) => Ok(CKDAppPublicKey::AppPublicKey(pk)),
            Helper::Tagged(Tagged::AppPublicKeyPV(pk)) => Ok(CKDAppPublicKey::AppPublicKeyPV(pk)),
            Helper::Plain(pk) => Ok(CKDAppPublicKey::AppPublicKey(pk)),
        }
    }
```
