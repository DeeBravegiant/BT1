### Title
Missing Output Validation for `AppPublicKey` Variant in `respond_ckd` Allows Single Byzantine Participant to Deliver Arbitrary CKD Output - (File: crates/contract/src/lib.rs)

### Summary

`respond_ckd` performs a cryptographic output check (`ckd_output_check`) only for the `AppPublicKeyPV` variant of `CKDAppPublicKey`. For the `AppPublicKey` (legacy) variant, **no verification is performed** that the submitted `CKDResponse` is the correct output for the stored `CKDRequest`. A single Byzantine attested participant can call `respond_ckd` with an arbitrary `CKDResponse` for any pending `AppPublicKey` CKD request, and the contract will accept and deliver it to the user without any cryptographic check.

### Finding Description

In `respond_ckd`, after confirming the caller is an attested participant, the contract branches on the request's `app_public_key` variant:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}   // ← no check at all
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
```

For `AppPublicKeyPV`, `ckd_output_check` verifies the pairing equation `e(big_c, g2) = e(big_y, app_pk2) · e(hash_point, public_key)`, binding the response to the master public key and the request's `app_id`. For `AppPublicKey`, the arm is an empty block — the response is accepted unconditionally. The contract then immediately resolves the pending yield with the unverified response bytes:

```rust
pending_requests::resolve_yields_for(
    &mut self.pending_ckd_requests,
    &request,
    serde_json::to_vec(&response).unwrap(),
)
```

The analog to the external report is exact: just as `RandomnessCommit` accepted an oracle account without checking it against `randomness.oracle`, `respond_ckd` accepts a `CKDResponse` without checking it against the stored `CKDRequest` when the `AppPublicKey` variant is used.

### Impact Explanation

A single Byzantine attested participant (strictly below the signing threshold) can:

1. Observe a pending `request_app_private_key` call that uses `CKDAppPublicKey::AppPublicKey`.
2. Construct an arbitrary `CKDResponse` — including one that encodes a confidential key the attacker controls or knows — encrypted under the user's public `app_public_key` (which is on-chain and public).
3. Call `respond_ckd(request, crafted_response)` before honest nodes respond.
4. The contract accepts the response, resolves the yield, and delivers the attacker-chosen output to the user.

The user's application receives a confidential key derivation output that was not produced by the MPC network's threshold computation. If the attacker crafts the ciphertext to encrypt a key they know (feasible since `app_public_key` is a public BLS12-381 G1 point and BLS ElGamal encryption is malleable), the attacker learns the user's "confidential" derived key. This constitutes unauthorized confidential key derivation output without the required participant authorization — a Critical impact under the allowed scope.

Even without key recovery, the attacker can deliver garbage, permanently failing the user's CKD request (the yield is resolved and removed from `pending_ckd_requests`, so no retry is possible for that yield slot).

### Likelihood Explanation

The `AppPublicKey` variant is described as "privately verifiable, legacy" but remains a live code path accepted by the contract. Any single attested participant — a role achievable by any operator who has submitted a valid TEE attestation — can execute this attack. No threshold collusion, no privileged operator access, and no key material beyond the attacker's own node credentials are required. The attacker only needs to race the honest nodes to submit `respond_ckd` first for a pending `AppPublicKey` request.

### Recommendation

Apply the same `ckd_output_check` (or an equivalent binding check) to the `AppPublicKey` variant, or remove the `AppPublicKey` variant from the live contract interface if it is truly deprecated. At minimum, add an explicit comment documenting why no check is performed and what trust assumption substitutes for it. If the design intent is that `AppPublicKey` responses are unverifiable on-chain, the contract should at least enforce that only the designated responding node (e.g., the one that observed the request) can submit the response, or require a threshold of participants to co-sign the response before it is accepted.

### Proof of Concept

1. User calls `request_app_private_key` with `CKDAppPublicKey::AppPublicKey(user_g1_pk)` and `domain_id` pointing to a BLS12-381 CKD domain.
2. The contract stores the pending request in `pending_ckd_requests` keyed by `CKDRequest { app_id, domain_id, ... }`.
3. Attacker (single attested participant) constructs `CKDResponse` encoding a BLS12-381 ciphertext of an attacker-chosen secret, encrypted under `user_g1_pk`.
4. Attacker calls `respond_ckd(request, crafted_response)`.
5. Contract executes:
   - `assert_caller_is_signer()` — passes (attacker is a signer).
   - `assert_caller_is_attested_participant_and_protocol_active()` — passes (attacker is attested).
   - `match &request.app_public_key { AppPublicKey(_) => {} ... }` — empty arm, no check.
   - `resolve_yields_for(...)` — resolves the yield with the crafted response.
6. User's `request_app_private_key` promise resolves with the attacker-chosen `CKDResponse`.
7. User's application decrypts the response and obtains the attacker-known key, believing it to be their MPC-derived confidential key.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crates/threshold-signatures/src/confidential_key_derivation/protocol_pv.rs (L221-247)
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

/// Check that `e(app_pk1, g2) = e(g1, app_pk2)`
fn app_public_key_check(app_pk: &PublicVerificationKey) -> bool {
    if !check_valid_point_g1(app_pk.pk1.into()) || !check_valid_point_g2(app_pk.pk2.into()) {
        return false;
    }
    multi_miller_loop(&[
        (app_pk.pk1, -ElementG2::generator()),
        (ElementG1::generator(), app_pk.pk2),
    ])
}
```
