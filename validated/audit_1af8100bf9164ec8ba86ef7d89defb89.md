### Title
Single Attested Participant Can Deliver Unverified CKD Response for `AppPublicKey` Variant, Bypassing Threshold Requirement - (File: crates/contract/src/lib.rs)

### Summary

`respond_ckd` performs no cryptographic verification of the `CKDResponse` when the request uses the `AppPublicKey` variant. A single attested participant (below the signing threshold) can call `respond_ckd` with an arbitrary fake `CKDResponse` for any pending `AppPublicKey` CKD request, causing the contract to immediately drain the yield queue and deliver the fabricated key material to the user — without any threshold of MPC nodes ever computing the derivation.

### Finding Description

`respond_ckd` in `crates/contract/src/lib.rs` handles two variants of `CKDAppPublicKey`:

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

For `AppPublicKeyPV`, `ckd_output_check` cryptographically binds the response to the domain public key and the app public key, so a single node cannot forge a valid response. For `AppPublicKey`, the arm is empty — any `CKDResponse` with any `big_y` and `big_c` values is accepted unconditionally.

After this match, `resolve_yields_for` is called:

```rust
pending_requests::resolve_yields_for(
    &mut self.pending_ckd_requests,
    &request,
    serde_json::to_vec(&response).unwrap(),
)
``` [2](#0-1) 

`resolve_yields_for` removes the request from `pending_ckd_requests` and calls `env::promise_yield_resume` for every queued yield, delivering the response to all waiting callers and permanently consuming the request slot: [3](#0-2) 

Once the attacker's call lands, the request is gone. Any subsequent honest `respond_ckd` call returns `Err(RequestNotFound)`.

The only guards in `respond_ckd` before the match are:
- `assert_caller_is_signer()` — caller must be the direct signer, not a proxy
- `is_running_or_resharing()` — protocol state
- `accept_requests` — TEE validation flag
- `assert_caller_is_attested_participant_and_protocol_active()` — caller must be an attested participant [4](#0-3) 

None of these checks verify that the `CKDResponse` content is the output of a threshold computation. The threshold is enforced off-chain by the MPC protocol, but the contract provides no on-chain backstop for `AppPublicKey` requests.

The analog to the BvB `reclaimContract()` bug is direct: BvB's function processed a reclaim without checking that the contract was legitimately matched (`bulls[contractId] != address(0)`); here, `respond_ckd` processes a CKD response without checking that the response is the legitimate output of a threshold computation. In both cases, a missing validation check allows an attacker to inject incorrect data into a flow that the protocol assumes is authenticated.

### Impact Explanation

A single Byzantine attested participant (strictly below the signing threshold) can:

1. Monitor the NEAR chain for `request_app_private_key` calls that use the `AppPublicKey` variant.
2. Immediately call `respond_ckd` with a fabricated `CKDResponse` — arbitrary `big_y` and `big_c` bytes — before the honest MPC nodes complete their threshold computation.
3. The contract accepts the call, drains the yield queue, and delivers the fake key material to the user.
4. The legitimate threshold-computed response can never be delivered; the request slot is permanently consumed.

The user receives incorrect key material (`big_c` that cannot be decrypted to the correct app private key). The threshold requirement — the core security property of the MPC network — is bypassed for all `AppPublicKey` CKD requests. This matches the allowed impact: *unauthorized confidential key derivation output without the required participant authorization* and *bypass of threshold-signature requirements*.

### Likelihood Explanation

Any single participant who has submitted a valid TEE attestation and is listed as an active participant can execute this attack. The attacker does not need to collude with other participants, compromise any key material, or perform any off-chain cryptographic work. The only prerequisite is being an attested participant, which is the normal operational state for every MPC node. The attack is a simple on-chain transaction race.

### Recommendation

Apply the same `ckd_output_check` used for `AppPublicKeyPV` to the `AppPublicKey` variant, or add an equivalent binding that proves the response is consistent with the domain public key and the request's `app_id`. If `AppPublicKey` is intentionally designed to skip on-chain verification (e.g., because the user verifies off-chain), the contract should at minimum verify that `big_y` is the correctly derived public key for the given `app_id` and domain, so that a fake response is detectable before the yield queue is drained.

Alternatively, require that `respond_ckd` can only be called after a threshold of participants have signed off on the response (e.g., by collecting votes before draining the yield), mirroring the threshold enforcement already present in `vote_pk` and `vote_reshared`.

### Proof of Concept

1. Honest user calls `request_app_private_key` with `AppPublicKey` variant; the request is inserted into `pending_ckd_requests`.
2. Attacker (a single attested participant) constructs a `CKDRequest` matching the pending request and a `CKDResponse` with arbitrary `big_y = [0u8; 48]` and `big_c = [0u8; 48]`.
3. Attacker calls `respond_ckd(fake_request, fake_response)`.
4. The `AppPublicKey` arm executes with no check; `resolve_yields_for` removes the request and resumes all yields with the fake response.
5. The user's `request_app_private_key` call resolves with the fake `CKDResponse`.
6. Honest nodes' subsequent `respond_ckd` calls return `Err(RequestNotFound)`.
7. The user receives `big_c` that cannot be decrypted to the correct app private key; the threshold computation never occurred. [5](#0-4) [6](#0-5)

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
