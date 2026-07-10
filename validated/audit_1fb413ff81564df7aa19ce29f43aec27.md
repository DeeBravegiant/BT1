### Title
Expired TEE Attestation Not Checked in `respond()` / `respond_verify_foreign_tx()` / `respond_ckd()` — Attestation Authorization Bypass - (File: crates/contract/src/lib.rs)

### Summary

The `respond`, `respond_ckd`, and `respond_verify_foreign_tx` contract methods validate that the caller is a current participant with matching TEE keys, but they do **not** verify that the caller's stored TEE attestation has not expired. A node whose attestation certificate has lapsed can continue submitting signing responses until `verify_tee()` is explicitly called by another participant. This is the direct analog of the Astaria H-19 pattern: a validation path checks authorization (key/membership) but silently skips the expiry check.

---

### Finding Description

Every `respond*` method delegates its caller-authorization check to `assert_caller_is_attested_participant_and_protocol_active()`:

```rust
// crates/contract/src/lib.rs  (respond)
self.assert_caller_is_attested_participant_and_protocol_active();
```

That helper calls only `is_caller_an_attested_participant`:

```rust
fn assert_caller_is_attested_participant_and_protocol_active(&self) {
    let participants = self.protocol_state.active_participants();
    Self::assert_caller_is_signer();
    let attestation_check = self
        .tee_state
        .is_caller_an_attested_participant(participants);
    assert_matches!(attestation_check, Ok(()), "Caller must be an attested participant");
}
```

`is_caller_an_attested_participant` verifies that the caller's account and TLS key match a stored `NodeId` entry, but it does **not** call `reverify_participants` (the function that checks `expiry_timestamp_seconds` against the current block time). The expiry check lives exclusively in `reverify_participants` / `reverify_and_cleanup_participants`, which are only invoked from `verify_tee()` — a separate, manually-triggered governance call.

The `accept_requests` flag is also checked in each `respond*` method, but it is only set to `false` after `verify_tee()` detects that removing expired participants would drop the active set below threshold. When the expired node count is small enough that resharing (not a full halt) is the response, `accept_requests` remains `true` throughout the window between expiry and the next `verify_tee()` call.

The two code paths that handle expiry are therefore completely decoupled from the respond path:

| Path | Checks expiry? |
|---|---|
| `assert_caller_is_attested_participant_and_protocol_active` → `is_caller_an_attested_participant` | **No** |
| `verify_tee` → `reverify_and_cleanup_participants` → `reverify_participants` | Yes |

---

### Impact Explanation

A TEE attestation expiry signals that the node's trusted-execution certificate is no longer valid — the node may no longer be running inside a genuine TEE enclave. The security model of the MPC network depends on every signing participant being TEE-attested at the time it contributes a signing share. If a node's attestation has expired and the node is compromised (e.g., the enclave is no longer isolated), it can:

1. Continue contributing signing shares to threshold computations, potentially leaking partial key material to an attacker who controls the host.
2. Submit a `respond_verify_foreign_tx` response that attests to a foreign-chain transaction state it did not actually verify inside a trusted enclave, enabling forged bridge execution or double-spend conditions.

Because the threshold is typically `t-of-n` with `t < n`, a single expired-attestation node below the threshold cannot forge signatures alone, but it can participate in legitimate signing rounds while its key share is exposed — and in the `verify_foreign_tx` flow it is the sole node that queries the foreign chain and signs the result, making a single compromised node sufficient for a forged bridge attestation.

**Allowed impact matched:** High — participant/attestation authorization bypass that causes invalid bridge execution or double-spend conditions.

---

### Likelihood Explanation

- TEE certificates have finite lifetimes (the `expiry_timestamp_seconds` field is populated from the certificate itself).
- `verify_tee()` is a manually-triggered governance call; there is no on-chain enforcement that it is called promptly after any attestation expires.
- An adversary who controls a node whose attestation has just expired has a concrete, bounded window (from expiry until the next `verify_tee()` call) to submit responses. The window length is entirely under the adversary's influence if they can delay or prevent other participants from calling `verify_tee()`.
- The `verify_foreign_tx` flow is particularly attractive because the node both queries the foreign chain and signs the result; a single expired-attestation node is sufficient to forge the attestation.

---

### Recommendation

Add an inline expiry check inside `assert_caller_is_attested_participant_and_protocol_active` (or directly in each `respond*` method) by calling `reverify_participants` for the caller's `NodeId` before accepting the response:

```rust
fn assert_caller_is_attested_participant_and_protocol_active(&self) {
    let participants = self.protocol_state.active_participants();
    Self::assert_caller_is_signer();

    // Existing membership + key-match check
    let attestation_check = self.tee_state.is_caller_an_attested_participant(participants);
    assert_matches!(attestation_check, Ok(()), "Caller must be an attested participant");

    // NEW: also verify the attestation has not expired
    let tee_upgrade_deadline = Duration::from_secs(self.config.tee_upgrade_deadline_duration_seconds);
    let caller_node_id = /* derive NodeId from signer + stored TLS key */;
    let status = self.tee_state.reverify_participants(&caller_node_id, tee_upgrade_deadline);
    assert_matches!(status, TeeQuoteStatus::Valid, "Caller TEE attestation has expired");
}
```

Alternatively, `is_caller_an_attested_participant` itself should be extended to re-verify the expiry timestamp as part of its lookup, so that the check is never accidentally omitted in future `respond*` methods.

---

### Proof of Concept

1. Node A is a legitimate participant; its TEE attestation has `expiry_timestamp_seconds = T`.
2. Block time advances past `T`. No participant calls `verify_tee()` yet; `accept_requests` remains `true`.
3. Node A (now with an expired attestation) calls `respond(request, response)`.
4. `assert_caller_is_attested_participant_and_protocol_active()` passes — it calls only `is_caller_an_attested_participant`, which checks key matching but not expiry.
5. `accept_requests` check passes (still `true`).
6. Signature validity check passes (Node A still holds a valid key share).
7. `pending_requests::resolve_yields_for` resolves the user's yield with the response — the signature is delivered to the user despite Node A's attestation being expired.

For `respond_verify_foreign_tx`, replace step 6 with: Node A queries the foreign chain from outside a trusted enclave and signs an arbitrary `payload_hash`, which the contract accepts because no expiry check is performed. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/contract/src/lib.rs (L563-581)
```rust
    #[handle_result]
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
```

**File:** crates/contract/src/lib.rs (L653-666)
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
```

**File:** crates/contract/src/lib.rs (L691-713)
```rust
    #[handle_result]
    pub fn respond_verify_foreign_tx(
        &mut self,
        request: VerifyForeignTransactionRequest,
        response: VerifyForeignTransactionResponse,
    ) -> Result<(), Error> {
        let signer = Self::assert_caller_is_signer();

        log!(
            "respond_verify_foreign_tx: signer={}, request={:?}",
            &signer,
            &request
        );

        self.assert_caller_is_attested_participant_and_protocol_active();

        if !self.protocol_state.is_running_or_resharing() {
            return Err(InvalidState::ProtocolStateNotRunning.into());
        }

        if !self.accept_requests {
            return Err(TeeError::TeeValidationFailed.into());
        }
```

**File:** crates/contract/src/lib.rs (L2389-2403)
```rust
    fn assert_caller_is_attested_participant_and_protocol_active(&self) {
        let participants = self.protocol_state.active_participants();

        Self::assert_caller_is_signer();

        let attestation_check = self
            .tee_state
            .is_caller_an_attested_participant(participants);

        assert_matches::assert_matches!(
            attestation_check,
            Ok(()),
            "Caller must be an attested participant"
        );
    }
```

**File:** crates/contract/src/tee/tee_state.rs (L969-1008)
```rust
    #[test]
    fn test_re_verify_rejects_expired_attestation() {
        // given
        let mut tee_state = TeeState::default();
        let node_id = NodeId {
            account_id: "about_to_be_expired.near".parse().unwrap(),
            tls_public_key: bogus_ed25519_public_key(),
            account_public_key: bogus_ed25519_public_key(),
        };

        const EXPIRY_TIMESTAMP_SECONDS: u64 = 1000;
        const ELAPSED_SECONDS: u64 = 200;

        testing_env!(VMContextBuilder::new().block_timestamp(0).build());

        let attestation = Attestation::Mock(MockAttestation::WithConstraints {
            mpc_docker_image_hash: None,
            launcher_docker_compose_hash: None,
            expiry_timestamp_seconds: Some(EXPIRY_TIMESTAMP_SECONDS),
            expected_measurements: None,
        });

        tee_state
            .add_participant(node_id.clone(), attestation, Duration::from_secs(0))
            .unwrap();

        // when
        testing_env!(
            VMContextBuilder::new()
                .block_timestamp(
                    Duration::from_secs(EXPIRY_TIMESTAMP_SECONDS + ELAPSED_SECONDS).as_nanos()
                        as u64
                )
                .build()
        );

        let status = tee_state.reverify_participants(&node_id, Duration::from_secs(0));

        // then
        assert_matches!(status, TeeQuoteStatus::Invalid(_));
```
