### Title
Missing CKD Output Verification for `AppPublicKey` Variant Allows Single Byzantine Participant to Deliver Unauthorized Key Derivation Response - (File: `crates/contract/src/lib.rs`)

---

### Summary

In `respond_ckd`, the contract performs a cryptographic output check (`ckd_output_check`) only when the request uses the `AppPublicKeyPV` variant. When the legacy `AppPublicKey` variant is used, the check is entirely skipped via an empty match arm. A single Byzantine attested participant — strictly below the signing threshold — can exploit this edge case to submit an arbitrary `CKDResponse` for any pending `AppPublicKey` CKD request, permanently consuming the request with attacker-controlled data before honest nodes complete the threshold protocol.

---

### Finding Description

In `crates/contract/src/lib.rs`, the `respond_ckd` function contains the following conditional output check:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}   // ← no check; any response accepted
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For `AppPublicKey` requests, the response is accepted without any cryptographic verification of its content. The function then unconditionally calls `resolve_yields_for`, which removes the pending request from `pending_ckd_requests` and resumes all queued yields with the attacker-supplied bytes:

```rust
pending_requests::resolve_yields_for(
    &mut self.pending_ckd_requests,
    &request,
    serde_json::to_vec(&response).unwrap(),
)
``` [2](#0-1) 

`resolve_yields_for` drains the entire fan-out queue in one pass and returns `Err(RequestNotFound)` for any subsequent call — meaning honest nodes that complete the threshold protocol afterward are silently rejected: [3](#0-2) 

This is structurally identical to the Connext `repayAavePortal` bug: a check exists for one code path (`AppPublicKeyPV`) but is absent for the edge case (`AppPublicKey`), allowing a malicious actor to bypass the invariant that only a threshold-authorized CKD output may be delivered.

The `AppPublicKey` variant is documented as "privately verifiable (legacy)" — the contract intentionally cannot verify it on-chain — but this design gap means the contract still accepts and irrevocably delivers any response submitted for such requests: [4](#0-3) 

---

### Impact Explanation

A single Byzantine attested participant (below signing threshold) can:

1. Observe a pending `request_app_private_key` call that uses the `AppPublicKey` variant.
2. Craft an arbitrary `CKDResponse` — for example, one that encrypts an attacker-chosen key using the user's publicly visible `app_public_key` (a BLS12-381 G1 point submitted in the original request).
3. Call `respond_ckd` with this fake response before honest nodes complete the threshold CKD computation.
4. The contract accepts the response without any check and calls `resolve_yields_for`, which drains the pending queue and delivers the attacker-controlled bytes to every queued caller.
5. All subsequent honest `respond_ckd` calls for the same request return `Err(RequestNotFound)` — the request is permanently consumed.

The user receives an attacker-controlled encrypted key. If the attacker encrypts a key they know under the user's `app_public_key`, the user's application will decrypt and use a key the attacker also possesses, giving the attacker full access to the user's application-layer secrets. This constitutes unauthorized confidential key derivation output delivered without the required threshold participant authorization.

**Impact class:** Critical — unauthorized CKD output without threshold authorization; potential theft of application-layer key material.

---

### Likelihood Explanation

Any single attested participant can execute this attack with no additional privileges. The attacker skips the actual threshold computation (which takes time for P2P coordination), so it has a structural speed advantage over honest nodes. The attack is deterministic and requires no brute force or cryptographic break. The `AppPublicKey` variant is described as "legacy" but remains fully supported and reachable by any user.

---

### Recommendation

1. **Deprecate `AppPublicKey` for new requests** and require `AppPublicKeyPV` for all new CKD submissions, since only `AppPublicKeyPV` allows the contract to enforce output correctness.
2. **Add a guard in `respond_ckd`** that rejects responses for `AppPublicKey` requests unless the caller can prove threshold agreement (e.g., by requiring a threshold-signed attestation of the response, or by migrating all CKD to the publicly verifiable path).
3. At minimum, **document explicitly** that `AppPublicKey` CKD requests provide no on-chain protection against a malicious responder, so integrators can make an informed choice.

---

### Proof of Concept

```
// Setup: contract is Running, attacker is an attested participant.

// Step 1: User submits a CKD request using the AppPublicKey (legacy) variant.
user.call("request_app_private_key", {
    derivation_path: "my-app",
    app_public_key: { AppPublicKey: "<bls12381g1:...>" },
    domain_id: 0,
}).deposit(1 yoctoNEAR);

// Step 2: Attacker observes the pending request on-chain.
// Attacker crafts a CKDResponse encrypting a key they know
// under the user's app_public_key (which is public).
let fake_response = CKDResponse { /* attacker-chosen encrypted key */ };

// Step 3: Attacker calls respond_ckd before honest nodes finish the threshold protocol.
attacker.call("respond_ckd", {
    request: <matching CKDRequest>,
    response: fake_response,
});

// Result: contract skips ckd_output_check (AppPublicKey branch),
// calls resolve_yields_for → drains pending_ckd_requests,
// delivers fake_response to the user.
// Honest nodes' subsequent respond_ckd calls → Err(RequestNotFound).
// User decrypts the response and uses an attacker-known key.
``` [5](#0-4)

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

**File:** crates/contract/README.md (L119-120)
```markdown
  - **Privately verifiable** (legacy): a single G1 point, e.g. `"bls12381g1:<base58>"` or `{"AppPublicKey": "bls12381g1:<base58>"}`.
  - **Publicly verifiable**: a pair of points `(pk1, pk2) = (a·G1, a·G2)`, passed as `{"AppPublicKeyPV": {"pk1": "bls12381g1:<base58>", "pk2": "bls12381g2:<base58>"}}`. This allows anyone to verify the encrypted result on-chain without the app's secret key.
```
