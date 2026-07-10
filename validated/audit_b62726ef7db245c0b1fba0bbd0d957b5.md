### Title
Unprivileged caller can remove active-participant attestations via `clean_invalid_attestations`, permanently stranding all in-flight signature requests - (File: `crates/contract/src/tee/tee_state.rs`)

---

### Summary

`clean_invalid_attestations` is a public, permissionless endpoint that removes entries from `stored_attestations` for any participant whose attestation has expired or whose image hash is no longer whitelisted. Because `respond()` requires the caller to pass `is_caller_an_attested_participant()` — which looks up the caller's TLS key in that same `stored_attestations` map — removing a participant's entry prevents them from ever calling `respond()`. Any unprivileged account can therefore drain the attestation map at the moment attestations expire, causing every in-flight yield-resume request to time out with no possibility of fulfillment until participants resubmit and the nodes retry.

---

### Finding Description

**Step 1 — Permissionless cleanup endpoint**

`clean_invalid_attestations` carries no `#[private]` guard and no participant/voter check: [1](#0-0) 

The only gate is that the protocol must be in `Running` state. Any NEAR account can call it with an arbitrarily large `max_scan`.

**Step 2 — Bulk removal of stored attestations**

The underlying implementation iterates `stored_attestations`, materialises every entry that fails `reverify_participants`, and removes them all in one pass: [2](#0-1) 

Crucially, the function does **not** check whether any of the removed participants have pending signature requests queued in `pending_signature_requests`, `pending_ckd_requests`, or `pending_verify_foreign_tx_requests`.

**Step 3 — `respond()` requires the caller to be in `stored_attestations`**

Every `respond*` entry point calls `assert_caller_is_attested_participant_and_protocol_active()`: [3](#0-2) 

That helper calls `is_caller_an_attested_participant()`, which performs a hard lookup against `stored_attestations`: [4](#0-3) 

If the participant's entry has been removed, `AttestationNotFound` is returned and the `assert_matches!` macro panics, reverting the entire `respond` transaction.

**Step 4 — Pending requests are stranded**

Pending yields are stored in `pending_signature_requests` (and the CKD / foreign-tx equivalents): [5](#0-4) 

`resolve_yields_for` — the only path to fulfil a pending request — is only reachable through `respond*`. With all participant attestations removed, no node can call `respond*`, so every queued yield sits until the ~200-block NEAR runtime timeout fires `return_signature_and_clean_state_on_success` with `Err(PromiseError::Failed)`: [6](#0-5) 

The user receives a timeout error and must resubmit. Any time-sensitive cross-chain operation (e.g., a Bitcoin HTLC with a deadline) that was waiting on the MPC signature is permanently broken.

---

### Impact Explanation

This is a **Medium** impact: request-lifecycle and participant-state manipulation that breaks the production safety invariant that in-flight signing requests are fulfillable by attested participants. An adversary can force every pending `sign`, `request_app_private_key`, or `verify_foreign_transaction` request to time out simultaneously, without any privileged access, by calling a single public endpoint at the moment participant attestations expire. For users executing time-sensitive foreign-chain transactions (e.g., HTLCs, atomic swaps), the timeout is not merely an inconvenience — it can cause irreversible loss of funds on the foreign chain even though the NEAR contract itself holds no funds.

---

### Likelihood Explanation

Attestations carry an expiry timestamp enforced by `reverify_participants`. On a live network, attestations expire periodically (the `tee_upgrade_deadline_duration` config controls the grace window). The adversary only needs to:

1. Monitor on-chain attestation expiry timestamps (all data is public).
2. Submit a batch of `sign()` requests just before expiry to fill the pending queue.
3. Call `clean_invalid_attestations(max_scan: u32::MAX)` immediately after expiry, before any node resubmits.

No key material, no collusion, no privileged role is required. The attack is repeatable every attestation cycle.

---

### Recommendation

`clean_invalid_attestations` should skip any participant whose `account_id` appears as the signer of at least one entry in any of the three pending-request maps, or alternatively restrict the endpoint to participants/voters only (matching the access control on `verify_tee`). A minimal fix is to add a participant-only guard:

```rust
pub fn clean_invalid_attestations(&mut self, max_scan: u32) -> Result<u32, Error> {
+   self.voter_or_panic(); // restrict to participants
    if !matches!(self.protocol_state, ProtocolContractState::Running(_)) {
        return Err(InvalidState::ProtocolStateNotRunning.into());
    }
    ...
}
```

A deeper fix would track which participants have outstanding pending yields and exclude them from the sweep until their yields are resolved or timed out.

---

### Proof of Concept

```
1. Contract is Running; participants A, B, C each have attestations expiring at block T.
2. At block T-1, attacker submits 10 sign() requests → 10 yields queued in
   pending_signature_requests.
3. At block T+1, attestations expire (reverify_participants returns Invalid).
4. Attacker calls clean_invalid_attestations(max_scan: 1000).
   → stored_attestations entries for A, B, C are removed.
5. Nodes A, B, C attempt respond() for the 10 queued requests.
   → is_caller_an_attested_participant() returns AttestationNotFound for each.
   → respond() panics; no yield is resumed.
6. After ~200 blocks, NEAR runtime fires return_signature_and_clean_state_on_success
   with Err(PromiseError::Failed) for each yield → all 10 requests time out.
7. Any foreign-chain transaction that depended on those signatures is now unrecoverable.
```

### Citations

**File:** crates/contract/src/lib.rs (L153-155)
```rust
    pending_signature_requests: LookupMap<SignatureRequest, Vec<YieldIndex>>,
    pending_ckd_requests: LookupMap<CKDRequest, Vec<YieldIndex>>,
    pending_verify_foreign_tx_requests: LookupMap<VerifyForeignTransactionRequest, Vec<YieldIndex>>,
```

**File:** crates/contract/src/lib.rs (L1824-1841)
```rust
    #[handle_result]
    pub fn clean_invalid_attestations(&mut self, max_scan: u32) -> Result<u32, Error> {
        log!(
            "clean_invalid_attestations: signer={}, max_scan={}",
            env::signer_account_id(),
            max_scan
        );
        // Running-only: keygen / resharing may reference attestations that have not yet
        // been activated, so cleanup is off-limits during those phases.
        if !matches!(self.protocol_state, ProtocolContractState::Running(_)) {
            return Err(InvalidState::ProtocolStateNotRunning.into());
        }
        let tee_upgrade_deadline_duration =
            Duration::from_secs(self.config.tee_upgrade_deadline_duration_seconds);
        Ok(self
            .tee_state
            .clean_invalid_attestations(tee_upgrade_deadline_duration, max_scan as usize))
    }
```

**File:** crates/contract/src/lib.rs (L2248-2271)
```rust
    #[private]
    pub fn return_signature_and_clean_state_on_success(
        &mut self,
        request: SignatureRequest,
        #[callback_result] signature: Result<dtos::SignatureResponse, PromiseError>,
    ) -> PromiseOrValue<dtos::SignatureResponse> {
        match signature {
            Ok(signature) => PromiseOrValue::Value(signature),
            Err(_) => {
                pending_requests::pop_oldest_pending_yield(
                    &mut self.pending_signature_requests,
                    &request,
                );

                let fail_on_timeout_gas = Gas::from_tgas(self.config.fail_on_timeout_tera_gas);
                let promise = Promise::new(env::current_account_id()).function_call(
                    method_names::FAIL_ON_TIMEOUT.to_string(),
                    vec![],
                    NearToken::from_near(0),
                    fail_on_timeout_gas,
                );
                near_sdk::PromiseOrValue::Promise(promise.as_return())
            }
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

**File:** crates/contract/src/tee/tee_state.rs (L406-433)
```rust
    pub fn clean_invalid_attestations(
        &mut self,
        tee_upgrade_deadline_duration: Duration,
        max_scan: usize,
    ) -> u32 {
        let has_invalid_attestation = |node_id: &NodeId| {
            !matches!(
                self.reverify_participants(node_id, tee_upgrade_deadline_duration),
                TeeQuoteStatus::Valid
            )
        };

        // Materialize candidates before any mutation to avoid iterator invalidation.
        let invalid_tls_keys: Vec<Ed25519PublicKey> = self
            .stored_attestations
            .iter()
            .take(max_scan)
            .filter(|(_, node_attestation)| has_invalid_attestation(&node_attestation.node_id))
            .map(|(tls_pk, _)| tls_pk.clone())
            .collect();

        let removed = u32::try_from(invalid_tls_keys.len())
            .expect("u32 should always be convertible from usize on wasm32");

        for tls_pk in invalid_tls_keys {
            self.stored_attestations.remove(&tls_pk);
        }
        removed
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
