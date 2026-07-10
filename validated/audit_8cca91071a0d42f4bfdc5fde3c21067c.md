### Title
Missing CKD Response Verification for `AppPublicKey` Variant Allows Single Byzantine Participant to Forge Key Derivation Output - (File: `crates/contract/src/lib.rs`)

---

### Summary

The `respond_ckd` function in `crates/contract/src/lib.rs` applies a cryptographic validity check (`ckd_output_check`) only for the `AppPublicKeyPV` variant of CKD requests, while silently accepting **any** response for the legacy `AppPublicKey` variant with no on-chain verification. A single Byzantine MPC participant (strictly below the signing threshold) can race the legitimate threshold computation and submit an arbitrary forged CKD response, which the contract accepts and delivers to the caller — bypassing the threshold requirement entirely.

---

### Finding Description

In `respond_ckd`, the contract branches on the request's `app_public_key` type:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}   // ← NO CHECK
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For `AppPublicKeyPV`, `ckd_output_check` cryptographically verifies that the submitted response is the correct BLS12-381 derived output for the given `app_id`, `app_pk`, and root public key — preventing any single node from forging the result. For `AppPublicKey` (the legacy "privately verifiable" variant), the arm is a no-op: the contract performs **zero** verification of the response bytes before calling `resolve_yields_for` and delivering the response to the caller. [2](#0-1) 

The access control on `respond_ckd` only requires the caller to be an attested participant — there is no threshold quorum check at the contract level:

```rust
let signer = Self::assert_caller_is_signer();
self.assert_caller_is_attested_participant_and_protocol_active();
``` [3](#0-2) 

The threshold is enforced only off-chain by the MPC protocol. A Byzantine participant who bypasses the off-chain protocol and calls `respond_ckd` directly faces no on-chain barrier for `AppPublicKey` requests.

The `AppPublicKey` variant is still accepted by the contract (it is not rejected or deprecated): [4](#0-3) 

---

### Impact Explanation

A single Byzantine MPC participant (below threshold) can:

1. Observe a pending `AppPublicKey` CKD request on-chain.
2. Immediately call `respond_ckd` with a self-chosen `CKDResponse` — e.g., a value encrypted under the app's public key (which is public) wrapping an attacker-chosen secret.
3. Because the contract performs no verification for `AppPublicKey`, the forged response is accepted and delivered to the caller via `resolve_yields_for`.
4. The caller's application decrypts the response and obtains the attacker-chosen key instead of the legitimately derived key.
5. The attacker, who chose the key, can decrypt any data the application subsequently encrypts under it.

This constitutes **unauthorized confidential key derivation output without the required participant authorization** — a Critical impact under the allowed scope. The threshold requirement (the core security property of the MPC network) is bypassed for all `AppPublicKey` CKD requests by a single participant.

---

### Likelihood Explanation

- The `AppPublicKey` variant is still accepted by the contract (described as "legacy" in the README but not gated or deprecated).
- The attacker has a **timing advantage**: the legitimate threshold computation requires multi-round MPC cooperation across participants; a single malicious node can submit a forged response in one transaction immediately after the request appears on-chain.
- The only barrier is TEE attestation (the attacker must be an attested participant), but the threat model explicitly includes Byzantine participants below threshold — i.e., a participant whose node software has been modified or whose TEE has been compromised.
- Once the forged response is accepted, `resolve_yields_for` removes the pending entry and resumes all queued yields; the legitimate nodes' subsequent response will fail with `RequestNotFound`, so the attack is irreversible. [5](#0-4) 

---

### Recommendation

1. **Deprecate and reject `AppPublicKey`**: Add an explicit `env::panic_str` for the `AppPublicKey` arm in both `request_app_private_key` and `respond_ckd`, requiring all callers to migrate to `AppPublicKeyPV`.
2. **If `AppPublicKey` must remain**: Require a threshold quorum of participants to each submit the same response before it is accepted (analogous to how `respond` for signatures requires a cryptographically verifiable result that implicitly encodes threshold cooperation), or add an off-chain commitment scheme that the contract can verify.
3. **Align with the `_safeMint` pattern**: Just as `AppPublicKeyPV` uses `ckd_output_check` to enforce on-chain verifiability, every CKD response path should have an equivalent safety gate before `resolve_yields_for` is called.

---

### Proof of Concept

```
1. User calls request_app_private_key({
       app_public_key: AppPublicKey(app_g1_point),
       domain_id: <ckd_domain>,
       derivation_path: "my-app/key-1"
   }) with 1 yoctoNEAR deposit.
   → Contract stores CKDRequest in pending_ckd_requests, parks yield.

2. Attacker (single Byzantine participant, attested) observes the request on-chain.
   Attacker constructs forged_response = CKDResponse { ... } where the encrypted
   payload wraps an attacker-chosen secret S, encrypted under app_g1_point.

3. Attacker calls respond_ckd(request, forged_response).
   → Contract enters AppPublicKey arm: no-op (zero verification).
   → resolve_yields_for resumes the caller's yield with forged_response.
   → pending_ckd_requests entry is removed.

4. Legitimate threshold computation completes; legitimate nodes call respond_ckd.
   → resolve_yields_for returns Err(RequestNotFound) — request already drained.

5. Caller decrypts forged_response using their BLS12-381 secret key,
   obtains attacker-chosen secret S.
   Attacker knows S and can decrypt all data the caller encrypts under it.
```

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

**File:** crates/contract/src/lib.rs (L655-666)
```rust
        let signer = Self::assert_caller_is_signer();
        log!("respond_ckd: signer={}, request={:?}", &signer, &request);

        if !self.protocol_state.is_running_or_resharing() {
            return Err(InvalidState::ProtocolStateNotRunning.into());
        }

        if !self.accept_requests {
            return Err(TeeError::TeeValidationFailed.into());
        }

        self.assert_caller_is_attested_participant_and_protocol_active();
```

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

**File:** crates/contract/src/lib.rs (L684-689)
```rust
        pending_requests::resolve_yields_for(
            &mut self.pending_ckd_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
    }
```

**File:** crates/contract/src/pending_requests.rs (L74-88)
```rust
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
