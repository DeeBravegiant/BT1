### Title
Missing Response Verification in `respond_ckd` for `AppPublicKey` Variant Allows Single Participant to Corrupt CKD Request Lifecycle — (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_ckd` performs no cryptographic verification of the submitted `CKDResponse` when the pending request uses the `AppPublicKey` variant. Any single attested participant — strictly below the signing threshold — can call `respond_ckd` with an arbitrary forged response, immediately resolving all pending yields for that request with garbage data. The threshold requirement for the CKD computation is bypassed at the contract level for this variant.

---

### Finding Description

In `respond_ckd`, the contract branches on the request's `app_public_key` variant to decide whether to verify the response:

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

For `AppPublicKeyPV`, the contract performs a BLS12-381 pairing check (`ckd_output_check`) that cryptographically proves the response was computed using the MPC network's master key and the user's ephemeral key pair. For `AppPublicKey`, the match arm is an empty block — the response is accepted unconditionally. [2](#0-1) 

The only gate on `respond_ckd` is `assert_caller_is_attested_participant_and_protocol_active`, which requires the caller to be a single attested participant in the current epoch — not a threshold of them. [3](#0-2) 

After the (absent) check, `resolve_yields_for` is called unconditionally, which resolves **all** queued yields for that request key with the submitted response: [4](#0-3) 

The analog to the external report is direct: just as `afterLockUpdate` lacked a check that only the authorized locker contract could call it, `respond_ckd` lacks a check that the submitted response is the threshold-authorized output. Any single participant can act as the "authorized responder" for `AppPublicKey` CKD requests.

---

### Impact Explanation

A single Byzantine participant (below the signing threshold) can:

1. Monitor the chain for pending `request_app_private_key` calls that use the `AppPublicKey` variant.
2. Race honest nodes by calling `respond_ckd` with an arbitrary `CKDResponse { big_y: <garbage>, big_c: <garbage> }`.
3. The contract accepts the forged response with no verification and resolves all pending yields for that request.
4. Every user who submitted that request receives garbage data and cannot derive their secret key.
5. The pending request entry is consumed — the user must resubmit, and the attacker can corrupt the retry indefinitely.

This breaks the production safety invariant that CKD outputs must be the result of a threshold computation. The `AppPublicKeyPV` variant is protected; the `AppPublicKey` variant is not.

**Impact class**: Medium — request-lifecycle and contract execution-flow manipulation that breaks production safety invariants without requiring network-level DoS or operator misconfiguration.

---

### Likelihood Explanation

- The `AppPublicKey` variant is a supported, documented production feature used in e2e tests.
- Only a single attested participant is required — well below the signing threshold.
- The attack is a simple race: submit `respond_ckd` before honest nodes. A malicious participant with low network latency wins reliably.
- No leaked keys, TEE breaks, or privileged access are required.

---

### Recommendation

1. **Preferred**: Deprecate the `AppPublicKey` variant and require all CKD requests to use `AppPublicKeyPV`, which has on-chain pairing verification. The `AppPublicKeyPV` path already enforces correctness.
2. **Alternative**: Implement a threshold-based response collection mechanism: buffer responses from participants and only resolve the yield once a threshold of participants have submitted the same `CKDResponse`.
3. **Minimum**: Document clearly that `AppPublicKey` CKD requests have no on-chain integrity guarantee and that a single malicious participant can corrupt them, so users relying on CKD for security-sensitive secrets should use `AppPublicKeyPV`.

---

### Proof of Concept

```
1. User calls request_app_private_key({
       derivation_path: "my/path",
       app_public_key: AppPublicKey(<user_g1_point>),
       domain_id: 0
   }) with 1 yoctoNEAR deposit.
   → Contract stores pending CKD request, yields data_id.

2. Malicious participant (account: "evil.near", attested) calls:
   respond_ckd(
       request = CKDRequest::new(AppPublicKey(<user_g1_point>), 0, user_account, "my/path"),
       response = CKDResponse { big_y: [0u8; 48], big_c: [0u8; 48] }
   )
   → Line 675-676: AppPublicKey arm is empty, no check runs.
   → Line 684-688: resolve_yields_for resolves all pending yields with garbage response.

3. User's yield-resume callback fires with CKDResponse { big_y: zeros, big_c: zeros }.
   User attempts to decrypt their secret using this response → fails.
   Pending request is gone; user must resubmit (attacker repeats step 2).
``` [5](#0-4) [6](#0-5)

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

**File:** crates/contract/src/tee/tee_state.rs (L469-498)
```rust
    pub(crate) fn is_caller_an_attested_participant(
        &self,
        participants: &Participants,
    ) -> Result<(), AttestationCheckError> {
        let signer_account_pk = env::signer_account_pk();
        let signer_id = env::signer_account_id();

        let info = participants
            .info(&signer_id)
            .ok_or(AttestationCheckError::CallerNotParticipant)?;

        let attestation = self
            .stored_attestations
            .get(&info.tls_public_key)
            .ok_or(AttestationCheckError::AttestationNotFound)?;

        if attestation.node_id.account_id != signer_id {
            return Err(AttestationCheckError::AttestationOwnerMismatch);
        }

        // Stored account keys are Ed25519 by construction; a non-Ed25519
        // signer necessarily mismatches.
        let signer_ed25519 = Ed25519PublicKey::try_from(&signer_account_pk)
            .map_err(|_| AttestationCheckError::AttestationKeyMismatch)?;
        if attestation.node_id.account_public_key != signer_ed25519 {
            return Err(AttestationCheckError::AttestationKeyMismatch);
        }

        Ok(())
    }
```
