### Title
Missing CKD Output Verification for `AppPublicKey` Variant Allows Byzantine Participant to Forge Confidential Key Derivation Output — (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_ckd` skips all cryptographic output verification when the pending request uses the `CKDAppPublicKey::AppPublicKey` variant. A single Byzantine attested participant (strictly below the signing threshold) can race to call `respond_ckd` with an arbitrary, attacker-chosen `CKDResponse`, and the contract will resolve the user's yield with that forged output — no threshold agreement required.

---

### Finding Description

In `respond_ckd` the contract branches on the request's `app_public_key` variant:

```rust
// crates/contract/src/lib.rs  lines 675-682
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← no check at all
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For `AppPublicKeyPV` the contract calls `ckd_output_check`, which cryptographically verifies that the returned `CKDResponse` is consistent with the user's application public key and the network's BLS12-381 master key. For `AppPublicKey` the arm is a no-op: the `app_pk` embedded in the variant is silently discarded and no equivalent check is performed.

Immediately after this branch the contract unconditionally resolves every queued yield for the request:

```rust
// crates/contract/src/lib.rs  lines 684-688
pending_requests::resolve_yields_for(
    &mut self.pending_ckd_requests,
    &request,
    serde_json::to_vec(&response).unwrap(),
)
``` [2](#0-1) 

`resolve_yields_for` drains **all** pending yields for the matching request key in one call, so the first `respond_ckd` that arrives wins and all waiting callers receive its payload.

The only gate before this path is `assert_caller_is_attested_participant_and_protocol_active`, which requires the caller to be a single attested participant — not a threshold quorum:

```rust
// crates/contract/src/lib.rs  lines 666
self.assert_caller_is_attested_participant_and_protocol_active();
``` [3](#0-2) 

The analog to GRB-09 is direct: in Gearbox, `creditAccount` was passed where `msg.sender` (the borrower) was required, causing the validation function to operate on the wrong entity. Here, the `app_pk` from `AppPublicKey` is present in the request but is never forwarded to `ckd_output_check` — the validation function is never called at all for this variant, producing the same class of "wrong/missing parameter in validation" defect.

---

### Impact Explanation

**Critical — confidential key derivation output without the required participant authorization.**

A Byzantine attested participant can:

1. Observe any pending `AppPublicKey` CKD request on-chain (the `pending_ckd_requests` map is readable).
2. Construct an arbitrary `CKDResponse` (`big_y`, `big_c`) of their choosing — for example, a key pair they control.
3. Call `respond_ckd` before honest nodes do. Because `resolve_yields_for` drains all yields on first call, the forged response is delivered to every waiting user.
4. The user's application receives a confidential key that the attacker knows (or that is cryptographically invalid), enabling decryption of the user's secrets or permanent loss of access to encrypted material.

No threshold agreement is needed; a single Byzantine node suffices.

---

### Likelihood Explanation

**Medium.** Every attested participant in the active set can call `respond_ckd`. A single compromised or malicious node — a realistic threat in any MPC deployment — can exploit this without colluding with others. The attack is a simple race: submit the forged response before honest nodes do. Because NEAR transaction ordering is deterministic and observable, a well-positioned attacker can reliably win the race.

---

### Recommendation

Apply the same `ckd_output_check` guard to the `AppPublicKey` arm. If the `AppPublicKey` variant intentionally omits a proof-of-validity (e.g., because the user's public key is not in a verifiable form), the contract must at minimum verify that the response is consistent with the BLS12-381 master public key and the request's `app_id` using whatever subset of checks is applicable. If no such check can be constructed for `AppPublicKey`, the variant should be removed or restricted to contexts where the full `AppPublicKeyPV` check is possible.

---

### Proof of Concept

```
Setup
─────
• Contract is Running with N attested participants (threshold T < N).
• User calls request_app_private_key with CKDAppPublicKey::AppPublicKey(user_pk).
  → pending_ckd_requests[ckd_request] = [yield_id]

Attack
──────
• Byzantine participant P (one of the N nodes, no collusion needed) observes
  the pending request on-chain.
• P constructs CKDResponse { big_y: attacker_key, big_c: attacker_enc }.
• P calls respond_ckd(ckd_request, forged_response).
  → Line 675: AppPublicKey arm → no-op, ckd_output_check never called.
  → Line 684: resolve_yields_for resolves the yield with forged_response.
• User's transaction resumes and receives CKDResponse containing attacker_key.
• Honest nodes' subsequent respond_ckd calls find no pending yield and are
  silently ignored (resolve_yields_for returns Ok on empty queue).

Result
──────
User holds a confidential key chosen by the attacker.
Attacker can decrypt any data the user encrypts under that key,
or the user permanently loses access if the attacker discards the key.
``` [4](#0-3)

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
