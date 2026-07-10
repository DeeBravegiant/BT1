### Title
Missing Cryptographic Verification of CKD Response for `AppPublicKey` Variant Allows Single Byzantine Participant to Forge Key Derivation Output - (File: `crates/contract/src/lib.rs`)

---

### Summary

The `respond_ckd` function in the MPC contract performs cryptographic verification of the CKD response **only** for the `AppPublicKeyPV` (publicly verifiable) variant. For the `AppPublicKey` (privately verifiable) variant, the verification branch is entirely empty. A single Byzantine attested participant — strictly below the signing threshold — can call `respond_ckd` with an arbitrary forged `CKDResponse` for any pending `AppPublicKey`-type request, and the contract will accept and deliver it to the waiting user.

---

### Finding Description

In `respond_ckd`, after confirming the caller is an attested participant and the protocol is active, the contract attempts to verify the response:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}   // ← NO VERIFICATION
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For `AppPublicKeyPV`, `ckd_output_check` verifies the pairing relationship between `big_y`, `big_c`, the app public key, and the network public key — confirming the output is a valid MPC-derived result. For `AppPublicKey`, the arm is a no-op (`{}`). The function then unconditionally calls `resolve_yields_for`, delivering the unverified response to all waiting callers: [2](#0-1) 

The `AppPublicKey` variant is the legacy "privately verifiable" form — the user holds a private key corresponding to the submitted public key and is supposed to decrypt the output themselves. Because the contract cannot perform the pairing check without the public G2 component, it performs no check at all. This is the analog of the ERC20 unprocessed return value: the result of the cryptographic verification function is not processed — the verification is entirely absent — for this variant.

By contrast, `respond` (for ECDSA/EdDSA signatures) always verifies the signature cryptographically before resolving yields: [3](#0-2) 

---

### Impact Explanation

A single Byzantine attested participant (strictly below the signing threshold) can:

1. Monitor the chain for pending `AppPublicKey`-type CKD requests.
2. Call `respond_ckd` with an arbitrary `CKDResponse` (e.g., `big_y = [0u8; 48]`, `big_c = [0u8; 48]`).
3. The contract passes all authorization checks (attested participant, protocol running, `accept_requests` true).
4. No cryptographic check is performed on the response.
5. `resolve_yields_for` delivers the forged key material to the user.

The user receives attacker-controlled key material instead of the legitimate MPC-derived confidential key. This constitutes **unauthorized confidential key derivation output without the required threshold participant authorization** — a single node can unilaterally forge the output that is supposed to require threshold agreement.

---

### Likelihood Explanation

The attacker must be an attested participant in the MPC network — not a random external caller. However, only **one** such participant is required, not a threshold-level coalition. The attack is straightforward: race the legitimate `respond_ckd` call by submitting a forged response first. Since `resolve_yields_for` drains the entire pending queue on the first successful call, the first valid `respond_ckd` wins and all queued yields receive that response. [4](#0-3) 

---

### Recommendation

For the `AppPublicKey` variant, on-chain pairing verification is not possible without the G2 component. Two mitigations are viable:

1. **Deprecate `AppPublicKey` for production CKD requests** and require `AppPublicKeyPV`, which provides on-chain verifiability. The `AppPublicKeyPV` path already enforces `ckd_output_check`.
2. **If `AppPublicKey` must be retained**, document explicitly that this variant provides no on-chain integrity guarantee and that users must verify the output themselves using their private key before trusting it.

---

### Proof of Concept

```
1. User calls request_app_private_key with AppPublicKey variant → pending CKD request queued.

2. Byzantine attested participant calls respond_ckd:
     request = <the pending CKDRequest>
     response = CKDResponse { big_y: [0u8; 48], big_c: [0u8; 48] }

3. Contract execution path in respond_ckd:
     assert_caller_is_signer()                          → passes (attested participant)
     is_running_or_resharing()                          → passes
     accept_requests                                    → passes
     assert_caller_is_attested_participant_...()        → passes
     public_key_extended(domain_id)                     → returns Bls12381 key
     match AppPublicKey(_) => {}                        → NO CHECK, falls through
     resolve_yields_for(pending_ckd_requests, ...)      → delivers forged response

4. User's yield-resume fires with CKDResponse { big_y: [0u8; 48], big_c: [0u8; 48] }.
   User receives attacker-controlled key material.
``` [5](#0-4)

### Citations

**File:** crates/contract/src/lib.rs (L602-644)
```rust
                // Check the signature is correct
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    payload_hash,
                    &expected_public_key,
                )
                .is_ok()
            }
            (
                dtos::SignatureResponse::Ed25519 { signature },
                PublicKeyExtended::Ed25519 {
                    edwards_point: public_key_edwards_point,
                    ..
                },
            ) => {
                let derived_public_key_edwards_point = derive_public_key_edwards_point_ed25519(
                    &public_key_edwards_point,
                    &request.tweak,
                );
                let derived_public_key_32_bytes =
                    dtos::Ed25519PublicKey::from(derived_public_key_edwards_point.compress());

                let message = request.payload.as_eddsa().expect("Payload is not EdDSA");

                near_mpc_signature_verifier::verify_eddsa_signature(
                    signature,
                    message,
                    &derived_public_key_32_bytes,
                )
                .is_ok()
            }
            (signature_response, public_key_requested) => {
                return Err(RespondError::SignatureSchemeMismatch {
                    mpc_scheme: Box::new(signature_response.clone()),
                    user_scheme: Box::new(public_key_requested),
                }
                .into());
            }
        };

        if !signature_is_valid {
            return Err(RespondError::InvalidSignature.into());
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
