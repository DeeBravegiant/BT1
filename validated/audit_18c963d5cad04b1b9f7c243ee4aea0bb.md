### Title
Inconsistent On-Chain Response Verification Between `respond` and `respond_ckd` (AppPublicKey Variant) — (`File: crates/contract/src/lib.rs`)

---

### Summary

The `respond` function verifies every submitted signature against the derived public key before resolving the yield. The `respond_ckd` function applies the same on-chain output check **only** for the `AppPublicKeyPV` (publicly verifiable) variant; for the `AppPublicKey` (privately verifiable) variant it performs **no cryptographic verification** of the submitted response. A single Byzantine attested participant acting as coordinator can therefore submit an arbitrary forged CKD response that the contract will unconditionally accept and deliver to the waiting caller.

---

### Finding Description

`respond` always verifies the submitted signature: [1](#0-0) 

`respond_ckd` branches on the key variant. For `AppPublicKeyPV` it calls `ckd_output_check`; for `AppPublicKey` the branch body is empty — the response is forwarded to `resolve_yields_for` without any cryptographic check: [2](#0-1) 

Both functions share the same caller-authentication guards (`assert_caller_is_signer`, `assert_caller_is_attested_participant_and_protocol_active`, `is_running_or_resharing`, `accept_requests`), so the only structural difference is the missing response-validity gate in the `AppPublicKey` arm of `respond_ckd`. [3](#0-2) 

---

### Impact Explanation

A single Byzantine attested participant (e.g., the round coordinator) can call `respond_ckd` with an arbitrary `CKDResponse` for any pending `AppPublicKey` CKD request. The contract resolves the NEAR yield immediately with the forged `(big_y, big_c)` pair. The requesting application decrypts the forged ciphertext with its ephemeral private key `a` and obtains a key `sig' = C' − a·Y'` that is entirely under the attacker's influence (up to the unknown scalar `a`). The legitimate nodes' correct responses are silently discarded because the yield is already resolved. This breaks the correctness invariant of the CKD protocol: the derived key no longer equals `msk · H(pk, app_id)`.

Matches allowed impact: **Medium — request-lifecycle and contract execution-flow manipulation that breaks production safety/accounting invariants.**

---

### Likelihood Explanation

- Requires only **one** Byzantine attested participant — well within the Byzantine fault model that MPC systems are designed to tolerate.
- The attacker must hold a valid TEE attestation accepted by the contract, which is a realistic condition for a compromised or malicious node operator.
- No network-level DoS, no threshold collusion, and no privileged operator access is required.
- The `AppPublicKey` (non-PV) variant is the **default/legacy** path documented in the README and CLI examples, making it the common code path. [4](#0-3) 

---

### Recommendation

Apply a symmetric defense-in-depth check in the `AppPublicKey` arm of `respond_ckd`. While full correctness cannot be verified on-chain without the app's ephemeral private key, the contract can at minimum:

1. **Validate that `big_y` and `big_c` are valid, non-identity BLS12-381 G1 points** before resolving the yield, rejecting trivially malformed responses.
2. **Document and surface the trust asymmetry** — callers using `AppPublicKey` accept a weaker on-chain guarantee than callers using `AppPublicKeyPV`, and should be guided toward the PV variant for production use.
3. **Consider requiring threshold agreement** (e.g., a quorum of attested participants must submit matching responses before the yield is resolved) to bring `respond_ckd` to the same security level as `respond`.

---

### Proof of Concept

1. User submits `request_app_private_key` with `AppPublicKey` variant; a NEAR yield is created.
2. Attacker (single attested participant, e.g., the coordinator) calls `respond_ckd(request, CKDResponse { big_y: attacker_Y, big_c: attacker_C })`.
3. Contract passes all guards (signer check, attestation check, state check) and reaches the `match` on `app_public_key`.
4. The `AppPublicKey(_) => {}` arm executes — no verification.
5. `resolve_yields_for` is called with the forged serialized response; the yield resolves immediately.
6. The user's callback receives `(attacker_Y, attacker_C)` and derives a key `sig' = attacker_C − a·attacker_Y`, which is not `msk · H(pk, app_id)`.
7. Legitimate nodes' subsequent `respond_ckd` calls find no pending yield and silently fail. [2](#0-1) [5](#0-4)

### Citations

**File:** crates/contract/src/lib.rs (L564-608)
```rust
    pub fn respond(
        &mut self,
        request: SignatureRequest,
        response: dtos::SignatureResponse,
    ) -> Result<(), Error> {
        let signer = Self::assert_caller_is_signer();

        log!("respond: signer={}, request={:?}", &signer, &request);

        self.assert_caller_is_attested_participant_and_protocol_active();

        if !self.protocol_state.is_running_or_resharing() {
            return Err(InvalidState::ProtocolStateNotRunning.into());
        }

        if !self.accept_requests {
            return Err(TeeError::TeeValidationFailed.into());
        }

        let domain = request.domain_id;
        let public_key = self.public_key_extended(domain)?;

        let signature_is_valid = match (&response, public_key) {
            (
                dtos::SignatureResponse::Secp256k1(signature_response),
                PublicKeyExtended::Secp256k1 { near_public_key },
            ) => {
                // generate the expected public key
                let secp_pk = dtos::Secp256k1PublicKey::try_from(&near_public_key)
                    .expect("Secp256k1 variant always has a secp256k1 key");
                let affine = *k256::PublicKey::try_from(&secp_pk)
                    .expect("stored key is always valid")
                    .as_affine();
                let expected_public_key =
                    derive_key_secp256k1(&affine, &request.tweak).map_err(RespondError::from)?;

                let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");

                // Check the signature is correct
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    payload_hash,
                    &expected_public_key,
                )
                .is_ok()
```

**File:** crates/contract/src/lib.rs (L654-689)
```rust
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

**File:** crates/contract/README.md (L119-121)
```markdown
  - **Privately verifiable** (legacy): a single G1 point, e.g. `"bls12381g1:<base58>"` or `{"AppPublicKey": "bls12381g1:<base58>"}`.
  - **Publicly verifiable**: a pair of points `(pk1, pk2) = (a·G1, a·G2)`, passed as `{"AppPublicKeyPV": {"pk1": "bls12381g1:<base58>", "pk2": "bls12381g2:<base58>"}}`. This allows anyone to verify the encrypted result on-chain without the app's secret key.
- `domain_id` (integer): identifies the master key to use for deriving the ckd, and must correspond to bls12381.
```
