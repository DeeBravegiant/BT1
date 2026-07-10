### Title
Unprivileged Caller Can Evict Active Participant Attestations via `clean_invalid_attestations`, Breaking Signing-Flow Invariant - (File: crates/contract/src/lib.rs)

---

### Summary

`MpcContract::clean_invalid_attestations` carries **no caller-authorization guard** and is explicitly documented as "Callable by anyone." It permanently removes entries from `stored_attestations` for any node whose attestation fails re-verification (expired or referencing a stale whitelist). Because every critical node-facing method — `respond`, `respond_ckd`, `respond_verify_foreign_tx`, `vote_pk`, `vote_reshared`, `start_keygen_instance`, `start_reshare_instance`, and `vote_abort_key_event_instance` — gates execution on `assert_caller_is_attested_participant_and_protocol_active`, which looks up the caller in `stored_attestations`, an unprivileged attacker who calls `clean_invalid_attestations` at the right moment can silently strip active participants of their ability to respond, stalling threshold-signature production without any privileged access.

---

### Finding Description

**Root cause — missing authorization in `clean_invalid_attestations`:**

```rust
// crates/contract/src/lib.rs  lines 1821-1841
/// Prunes up to `max_scan` stored attestations that fail re-verification …
/// Callable by anyone while the protocol is in `Running`.
#[handle_result]
pub fn clean_invalid_attestations(&mut self, max_scan: u32) -> Result<u32, Error> {
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

No `voter_or_panic()`, no `assert_caller_is_signer()`, no participant check — any NEAR account may call this.

**What the inner function does:**

```rust
// crates/contract/src/tee/tee_state.rs  lines 406-434
pub fn clean_invalid_attestations(…) -> u32 {
    let invalid_tls_keys: Vec<Ed25519PublicKey> = self
        .stored_attestations
        .iter()
        .take(max_scan)
        .filter(|(_, node_attestation)| has_invalid_attestation(&node_attestation.node_id))
        .map(|(tls_pk, _)| tls_pk.clone())
        .collect();
    for tls_pk in invalid_tls_keys {
        self.stored_attestations.remove(&tls_pk);   // ← permanent deletion
    }
    …
}
```

The re-verification predicate (`reverify_participants`) returns `TeeQuoteStatus::Invalid` for any attestation whose expiry timestamp has passed — a natural, time-driven event that every node will eventually reach between periodic re-submissions.

**The invariant that is broken:**

Every node-facing mutating method requires attestation presence:

```rust
// crates/contract/src/lib.rs  lines 2389-2403
fn assert_caller_is_attested_participant_and_protocol_active(&self) {
    let participants = self.protocol_state.active_participants();
    Self::assert_caller_is_signer();
    let attestation_check = self
        .tee_state
        .is_caller_an_attested_participant(participants);
    assert_matches!(attestation_check, Ok(()), "Caller must be an attested participant");
}
```

`is_caller_an_attested_participant` resolves the caller's signer key against `stored_attestations`. Once `clean_invalid_attestations` removes a participant's entry, that participant's calls to `respond`, `vote_pk`, `vote_reshared`, etc. all panic with "Caller must be an attested participant," even though the participant is still listed in the protocol's `ThresholdParameters`.

**The accounting invariant broken (analog to dPrime burn):**

| dPrime analog | NEAR MPC analog |
|---|---|
| Admin burns user tokens | Anyone removes participant attestation |
| Token supply < LMCV recorded debt | `stored_attestations` entries < active participant set |
| User cannot repay debt | Participant cannot submit `respond()` |
| LMCV accounting broken | Signing-flow accounting broken |

The contract's `pending_signature_requests` map still holds live yield-resume promises for in-flight requests, but the participants whose attestations were removed can no longer call `respond` to fulfill them. Those yields will time out, and users receive `RequestError::Timeout` instead of a signature — a permanent freeze of those requests.

**Attack path (no privilege required):**

1. Attacker monitors the chain. Participant attestations have a finite `expiry_timestamp_seconds`.
2. When one or more participants' attestations cross their expiry (a routine, predictable event), the attacker calls:
   ```
   clean_invalid_attestations({ max_scan: 4294967295 })
   ```
   from any NEAR account, with no deposit and minimal gas.
3. `stored_attestations` entries for the expired participants are permanently deleted.
4. Those participants can no longer call `respond()`, `vote_pk()`, `vote_reshared()`, etc.
5. If the number of affected participants drops the effective responder count below the reconstruction threshold, no further threshold signatures can be produced until participants re-submit attestations — a window that can be extended by the attacker calling `clean_invalid_attestations` again immediately after each re-submission attempt if the re-submitted attestation is still within the expiry grace period but the attacker races the cleanup.

---

### Impact Explanation

**Medium — participant-state and contract execution-flow manipulation that breaks production safety invariants.**

- Active participants are stripped of their ability to call `respond()`, `respond_ckd()`, `respond_verify_foreign_tx()`, `vote_pk()`, `vote_reshared()`, `start_keygen_instance()`, and `start_reshare_instance()` without any governance action or threshold collusion.
- In-flight `pending_signature_requests` yield promises are orphaned: the participants who should fulfill them can no longer do so, causing user-facing request timeouts.
- If the attacker targets enough participants simultaneously (e.g., all nodes whose attestations expire in the same epoch), the network loses the ability to produce threshold signatures entirely until re-submission completes — a production safety invariant violation.
- No privileged key, TEE access, or operator action is required.

---

### Likelihood Explanation

**Low-to-Medium.** Attestation expiry is a routine, predictable, on-chain-observable event. The attack window (between expiry and re-submission) is narrow under normal operations but widens during network upgrades, node restarts, or any period when nodes are slow to re-attest. An attacker who monitors `get_tee_accounts()` and block timestamps can time the call precisely. The cost is a single NEAR transaction with no deposit.

---

### Recommendation

Add a caller-authorization guard to `clean_invalid_attestations` consistent with the pattern used by every other state-mutating method:

```rust
#[handle_result]
pub fn clean_invalid_attestations(&mut self, max_scan: u32) -> Result<u32, Error> {
    // Restrict to participants or the contract itself (post-reshare self-call).
    let caller = env::predecessor_account_id();
    let is_self_call = caller == env::current_account_id();
    if !is_self_call {
        self.voter_or_panic(); // panics if caller is not a current participant
    }
    …
}
```

Alternatively, scope the sweep so it never removes attestations belonging to accounts that are currently listed in `ThresholdParameters::participants()`, regardless of expiry — preserving the invariant that active participants always retain their ability to call `respond()`.

---

### Proof of Concept

```rust
// Attacker account (any NEAR account, no special role):
// 1. Observe that participant P's attestation has expired via get_tee_accounts() + block timestamp.
// 2. Call:
contract.clean_invalid_attestations(u32::MAX)
// Returns: 1 (one entry removed — participant P's attestation)

// 3. Participant P attempts to submit a signature response:
contract.respond(request, valid_signature_response)
// Panics: "Caller must be an attested participant"

// 4. User's pending sign() yield times out → RequestError::Timeout
```

The sandbox test `clean_invalid_attestations__should_remove_expired_entries` (crates/contract/tests/sandbox/tee.rs:360-430) already demonstrates that any account (the test uses `contract.as_account()`) can call this endpoint and successfully evict entries — including entries belonging to current participants — confirming the reachable attack path. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/contract/src/lib.rs (L563-573)
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
```

**File:** crates/contract/src/lib.rs (L1821-1841)
```rust
    /// Prunes up to `max_scan` stored attestations that fail re-verification (expired or
    /// referencing stale whitelists). Returns the number of entries removed. Callable by
    /// anyone while the protocol is in `Running`.
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

**File:** crates/contract/src/tee/tee_state.rs (L406-434)
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
    }
```

**File:** crates/contract/tests/sandbox/tee.rs (L414-423)
```rust
    // When: any account calls `clean_invalid_attestations` with a scan budget large enough
    // to cover every stored entry.
    let scan_budget: u32 = (before_cleanup.len() as u32) + 1;
    let result = contract
        .as_account()
        .call(contract.id(), method_names::CLEAN_INVALID_ATTESTATIONS)
        .args_json(serde_json::json!({ "max_scan": scan_budget }))
        .transact()
        .await?;
    assert!(result.is_success());
```
