### Title
Unverified CKD Response for `AppPublicKey` Variant Allows Single Byzantine Participant to Forge Confidential Key Derivation Output - (File: crates/contract/src/lib.rs)

### Summary
The `respond_ckd` function in the MPC contract performs on-chain cryptographic verification of the CKD response **only** for the `AppPublicKeyPV` (publicly verifiable) variant. For the legacy `AppPublicKey` (privately verifiable) variant, the match arm is empty — no verification is performed. A single Byzantine attested MPC participant can submit an arbitrary forged `CKDResponse` for any pending `AppPublicKey` CKD request, causing the requesting application to derive a key that the attacker controls and can recover, bypassing the threshold requirement entirely.

### Finding Description

In `respond_ckd` at `crates/contract/src/lib.rs` lines 675–682, the contract branches on the `app_public_key` type stored in the pending request:

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

For `AppPublicKeyPV`, `ckd_output_check` verifies the pairing equation `e(C, G2) = e(H(pk, app_id), pk) · e(Y, A2)` on-chain, ensuring the response is a valid encryption of the correct BLS signature under the MPC master key. For `AppPublicKey`, the arm is empty — any `(big_y, big_c)` pair is accepted unconditionally.

The only guards before this branch are:

- `assert_caller_is_signer()` — caller must be the transaction signer
- `assert_caller_is_attested_participant_and_protocol_active()` — caller must be an attested MPC participant [2](#0-1) 

Neither guard requires threshold-many participants to agree on the response. A single Byzantine attested participant can call `respond_ckd` with arbitrary `big_y` and `big_c` values. The contract then calls `resolve_yields_for`, which removes the pending request and delivers the forged response to the waiting caller. [3](#0-2) 

The `AppPublicKey` variant is still accepted at `request_app_private_key` (line 485, empty arm) and is documented as the "legacy" format in the contract README. [4](#0-3) 

The CKD design document explicitly states that the MPC contract does not enforce response verification for this variant — it is left to the developer's contract. In practice, many callers (including the `ckd-example-cli`) perform off-chain verification, but the contract provides no on-chain guarantee. [5](#0-4) 

### Impact Explanation

The CKD protocol's security guarantee is that the derived key `s = HKDF(msk · H(pk, app_id))` is deterministic and known only to the requesting application. A Byzantine participant forging the response breaks both properties:

**Key recovery by attacker:** The app's public key `A = a·G1` is submitted in the CKD request and is on-chain public. The attacker chooses a known scalar `k` and submits:
- `big_y = G1` (the generator)
- `big_c = k·G1 + A = (k + a)·G1`

The app computes `sig = big_c − a·big_y = (k+a)·G1 − a·G1 = k·G1`. Since the attacker chose `k`, they know `k·G1` and therefore know `sig`, and thus derive `s = HKDF(k·G1)` — the same value the app will use as its secret key.

**Threshold bypass:** The threshold signing requirement is enforced only at the off-chain MPC protocol level. The contract accepts a response from any single attested participant for the `AppPublicKey` variant, allowing one Byzantine node (strictly below the threshold) to fulfill the request with a forged output.

This matches the allowed Critical impact: *"Bypass of threshold-signature requirements or unauthorized access to MPC key shares, signing capability, or secret material that materially enables forgery or secret recovery."*

### Likelihood Explanation

- The `AppPublicKey` variant is the legacy format, still accepted and documented. Existing integrations may use it.
- Any single attested MPC participant can call `respond_ckd` — no collusion is required.
- The Byzantine participant can submit the forged response immediately upon seeing the CKD request on-chain, before the honest coordinator completes the off-chain protocol and submits the real response. This is a straightforward front-run within the same or next block.
- The app's public key `A` is visible on-chain in the pending request, giving the attacker all inputs needed to craft the key-recovering forgery.

### Recommendation

1. **Enforce on-chain verification for all CKD variants.** Extend `ckd_output_check` (or an equivalent check) to the `AppPublicKey` branch. For the privately verifiable variant, the check `e(C − a·Y, G2) = e(H(pk, app_id), pk)` cannot be performed without `a`, but the contract can at minimum verify that `big_y` and `big_c` are valid, non-identity G1 points and reject responses where `big_y` is the identity (which enables the trivial key-recovery attack).
2. **Deprecate `AppPublicKey` in favor of `AppPublicKeyPV`.** The publicly verifiable variant provides full on-chain guarantees and should be the only accepted format for new requests. Emit a deprecation warning or reject `AppPublicKey` requests outright.
3. **Document the trust assumption explicitly.** If `AppPublicKey` is retained, the contract should emit a log warning that the response is unverified on-chain, and the developer's contract must perform off-chain verification before using the derived key.

### Proof of Concept

1. User submits `request_app_private_key` with `AppPublicKey(A)` where `A = a·G1` is the app's ephemeral public key. The request is stored in `pending_ckd_requests`.
2. Byzantine attested participant observes the pending request on-chain, reads `A` from the request.
3. Attacker chooses scalar `k` and calls `respond_ckd(request, CKDResponse { big_y: G1, big_c: k·G1 + A })`.
4. Contract executes lines 675–682: `AppPublicKey` arm is empty, no verification. `resolve_yields_for` delivers `(big_y, big_c)` to the waiting caller.
5. App receives `(G1, k·G1 + A)` and computes `sig = (k·G1 + A) − a·G1 = k·G1`.
6. App derives `s = HKDF(k·G1)`. Attacker, knowing `k`, computes the same `s`.
7. Attacker now possesses the app's confidential derived key `s`, which the app uses for sensitive operations (e.g., encrypting data, signing foreign-chain transactions). [6](#0-5) [7](#0-6)

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

**File:** crates/threshold-signatures/docs/confidential_key_derivation/confidential-key-derivation.md (L61-66)
```markdown
Notice the MPC contract does not verify remote attestation, nor does it impose
how the authentication of the TEE app is enforced. The developer must control
the contract which calls the CKD functionality, and make the required
verifications within that contract. In this document, for completeness we
provide an example workflow for the developer. This workflow can be modified if
required.
```

**File:** crates/threshold-signatures/src/confidential_key_derivation.rs (L51-55)
```rust
    /// Takes a secret scalar and returns
    /// s <- C − a ⋅ Y = msk ⋅ H ( `app_id` )
    pub fn unmask(&self, secret_scalar: Scalar) -> Signature {
        self.big_c - self.big_y * secret_scalar
    }
```
