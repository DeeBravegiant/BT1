### Title
No-Domain Fast-Path State Transition Skips Post-Resharing Cleanup, Allowing Stale Governance Votes to Persist and Bypass Threshold Requirements — (File: `crates/contract/src/state/running.rs`)

---

### Summary

When `vote_new_parameters` is called while the contract has no registered domains, `transition_to_resharing_no_checks` mutates the `RunningContractState` in-place and returns `None`, bypassing the `Resharing` state entirely. Because `vote_reshared` is never reached, the six post-resharing cleanup promises it spawns — including `CLEAN_TEE_STATUS` and `REMOVE_NON_PARTICIPANT_TEE_VERIFIER_VOTES` — are never scheduled. Governance votes cast by participants who are subsequently removed from the set persist in storage and continue to count toward the threshold in future `vote_code_hash` and `vote_tee_verifier_change` calls, allowing a Byzantine minority (strictly below the signing threshold) to pre-stage votes that later combine with new-participant votes to reach the governance threshold with fewer legitimate votes than the protocol requires.

---

### Finding Description

**Root cause — the no-domain fast path:**

In `crates/contract/src/state/running.rs`, `transition_to_resharing_no_checks` has two branches:

```rust
// running.rs:66-101
pub fn transition_to_resharing_no_checks(
    &mut self,
    proposal: &ProposedThresholdParameters,
) -> Option<ResharingContractState> {
    if let Some(first_domain) = self.domains.get_domain_by_index(0) {
        // Normal path: returns Some(ResharingContractState)
        // → eventually vote_reshared() is called → cleanup promises spawned
        Some(ResharingContractState { ... })
    } else {
        // No-domain fast path: mutates self in-place, returns None
        // → vote_reshared() is NEVER called → NO cleanup promises spawned
        *self = RunningContractState::new(
            self.domains.clone(),
            Keyset::new(self.keyset.epoch_id.next(), Vec::new()),
            proposal.parameters().clone(),
            self.add_domains_votes.clone(),
        );
        None
    }
}
``` [1](#0-0) 

When `None` is returned, `state.rs::vote_new_parameters` maps it to `None`, and `lib.rs::vote_new_parameters` does not update `self.protocol_state`. The state change happens silently inside the `RunningContractState` value, but the caller in `lib.rs` never enters the `if let Some(new_state)` branch.

**What the normal path does that the fast path skips:**

In `lib.rs`, `vote_reshared` spawns six cleanup promises upon a successful resharing:

```rust
// lib.rs:1170-1236
if let Some(new_state) = self.protocol_state.vote_reshared(key_event_id)? {
    self.protocol_state = new_state;
    // ...
    Promise::new(env::current_account_id())
        .function_call(method_names::CLEAN_TEE_STATUS.to_string(), ...)  // cleans stale code-hash votes
        .detach();
    // ...
    Promise::new(env::current_account_id())
        .function_call(method_names::REMOVE_NON_PARTICIPANT_TEE_VERIFIER_VOTES.to_string(), ...)
        .detach();
    // + 4 more cleanup promises
}
``` [2](#0-1) 

None of these are spawned in the no-domain fast path.

**Why stale votes matter — the missing filter:**

The comment inside `vote_reshared` explicitly singles out `vote_update` as safe:

> `// Note: MpcContract::vote_update uses filtering to ensure correctness even if this cleanup fails.` [3](#0-2) 

`vote_update` is safe because it re-filters votes against the current participant set at call time:

```rust
// lib.rs:1363-1374
let valid_votes_count = running_state
    .parameters
    .participants()
    .participants()
    .iter()
    .filter(|(account_id, _, _)| {
        self.proposed_updates
            .vote_by_participant
            .get(account_id)
            .is_some_and(|voted_id| *voted_id == id)
    })
    .count();
``` [4](#0-3) 

`vote_code_hash` and `vote_tee_verifier_change` have **no such filter**. They authenticate the *caller* against current participants but then pass the raw vote count returned by `tee_state.vote()` / `tee_verifier_votes.vote()` directly to the threshold check:

```rust
// lib.rs:1417-1428
let participant = AuthenticatedParticipantId::new(threshold_parameters.participants())?;
let votes = self.tee_state.vote(code_hash, &participant);
if votes >= self.threshold()?.value() {
    self.tee_state.whitelist_tee_proposal(code_hash, tee_upgrade_deadline_duration);
}
``` [5](#0-4) 

The existence of `clean_non_participant_votes` (called by `CLEAN_TEE_STATUS`) and `tee_verifier_votes.retain(participants)` (called by `REMOVE_NON_PARTICIPANT_TEE_VERIFIER_VOTES`) confirms that these vote stores accumulate stale entries and rely on the cleanup to remove them. [6](#0-5) [7](#0-6) 

---

### Impact Explanation

After the no-domain fast path transition, stale votes from removed participants remain in `tee_state` (code-hash votes) and `tee_verifier_votes` (verifier-change votes). The new participant set may have a lower governance threshold than the old one. A single new-participant call to `vote_code_hash` or `vote_tee_verifier_change` for the same proposal will add to the stale count; if the combined total meets the new (lower) threshold, the proposal is accepted with fewer legitimate current-participant votes than the protocol requires.

A whitelisted malicious code hash allows nodes running that image to submit attestations that pass `submit_participant_info`, be admitted as participants, and subsequently participate in threshold signing — enabling unauthorized signature issuance. This matches the **Medium** allowed impact: *participant-state or contract execution-flow manipulation that breaks production safety/accounting invariants*, with a downstream path to **Critical** unauthorized threshold signature issuance.

---

### Likelihood Explanation

The no-domain state is the **initial state** of every freshly deployed contract (`init` creates `RunningContractState` with `DomainRegistry::default()` and an empty keyset). The attack window is open from deployment until the first successful `vote_add_domains` + `vote_pk` cycle completes. During this window:

1. A Byzantine minority of initial participants (strictly below the old governance threshold) pre-votes for a malicious code hash.
2. Honest participants (or the attacker's own coalition) reach the governance threshold for a `vote_new_parameters` call that changes the participant set.
3. The no-domain fast path fires; stale votes are not cleaned up.
4. The new (potentially lower) threshold is now reachable by combining stale votes with a single new-participant vote.

No privileged access, no TEE attack, and no network-level DoS is required. The attacker only needs to be a participant in the initial set and coordinate a participant-set change.

---

### Recommendation

In `lib.rs::vote_new_parameters`, detect when the no-domain fast path was taken (i.e., the protocol state is still `Running` after the call but the epoch ID has advanced) and spawn the same cleanup promises that `vote_reshared` spawns. Alternatively — and more robustly — add per-call filtering to `vote_code_hash` and `vote_tee_verifier_change` analogous to the filter already present in `vote_update`, so that only votes from current participants are counted regardless of whether cleanup has run.

---

### Proof of Concept

1. Contract is deployed via `init` with participants **A, B, C, D, E** (governance threshold = 4). No domains exist yet.
2. **D** and **E** (malicious; 2 of 5, below threshold 4) call `vote_code_hash(H)` for a malicious image hash `H`. Two votes are stored in `tee_state`; threshold 4 is not reached.
3. All five participants call `vote_new_parameters` proposing the new set **{A, B, C}** with threshold 2. Four votes are cast; threshold 4 is reached.
4. `transition_to_resharing_no_checks` fires the no-domain branch: `RunningContractState` is updated in-place to `{A, B, C, threshold=2}`. `vote_reshared` is never called; `CLEAN_TEE_STATUS` is never scheduled. D and E's votes for `H` remain in `tee_state`.
5. **A** (honest, now a current participant) calls `vote_code_hash(H)`. `tee_state.vote(H, A)` returns 3 (D + E + A). `3 >= threshold(2)` → `whitelist_tee_proposal(H)` executes.
6. A malicious node running image `H` calls `submit_participant_info` with a valid-looking attestation. The whitelisted hash passes verification; the node is admitted.
7. The malicious node participates in future key-generation or signing rounds, enabling unauthorized threshold signature issuance. [8](#0-7) [9](#0-8) [5](#0-4)

### Citations

**File:** crates/contract/src/state/running.rs (L66-101)
```rust
    pub fn transition_to_resharing_no_checks(
        &mut self,
        proposal: &ProposedThresholdParameters,
    ) -> Option<ResharingContractState> {
        if let Some(first_domain) = self.domains.get_domain_by_index(0) {
            let epoch_id = self.prospective_epoch_id();

            Some(ResharingContractState {
                previous_running_state: RunningContractState::new(
                    self.domains.clone(),
                    self.keyset.clone(),
                    self.parameters.clone(),
                    self.add_domains_votes.clone(),
                ),
                reshared_keys: Vec::new(),
                resharing_key: KeyEvent::new(
                    epoch_id,
                    first_domain.clone(),
                    proposal.parameters().clone(),
                ),
                cancellation_requests: HashSet::new(),
                per_domain_thresholds: proposal.per_domain_thresholds().clone(),
            })
        } else {
            // New parameters were proposed, but we have no keys, so directly
            // transition into Running state but bump the EpochId. With no
            // domains the per-domain threshold updates have nothing to apply to
            // and are dropped.
            *self = RunningContractState::new(
                self.domains.clone(),
                Keyset::new(self.keyset.epoch_id.next(), Vec::new()),
                proposal.parameters().clone(),
                self.add_domains_votes.clone(),
            );
            None
        }
```

**File:** crates/contract/src/lib.rs (L1170-1236)
```rust
        if let Some(new_state) = self.protocol_state.vote_reshared(key_event_id)? {
            // Resharing has concluded, transition to running state
            self.protocol_state = new_state;
            self.recompute_available_foreign_chains();

            // Spawn a promise to clean up votes from non-participants.
            // Note: MpcContract::vote_update uses filtering to ensure correctness even if this cleanup fails.
            Promise::new(env::current_account_id())
                .function_call(
                    method_names::REMOVE_NON_PARTICIPANT_UPDATE_VOTES.to_string(),
                    vec![],
                    NearToken::from_yoctonear(0),
                    Gas::from_tgas(self.config.remove_non_participant_update_votes_tera_gas),
                )
                .detach();
            // Spawn a promise to drop votes cast by non-participants.
            Promise::new(env::current_account_id())
                .function_call(
                    method_names::CLEAN_TEE_STATUS.to_string(),
                    vec![],
                    NearToken::from_yoctonear(0),
                    Gas::from_tgas(self.config.clean_tee_status_tera_gas),
                )
                .detach();
            // Spawn a bounded sweep over stored attestations to prune invalid / expired entries.
            Promise::new(env::current_account_id())
                .function_call(
                    method_names::CLEAN_INVALID_ATTESTATIONS.to_string(),
                    serde_json::to_vec(&serde_json::json!({
                        "max_scan": RESHARE_CLEAN_INVALID_ATTESTATIONS_MAX_SCAN
                    }))
                    .unwrap(),
                    NearToken::from_yoctonear(0),
                    Gas::from_tgas(self.config.clean_invalid_attestations_tera_gas),
                )
                .detach();
            // Spawn a promise to clean up orphaned node migrations for non-participants
            Promise::new(env::current_account_id())
                .function_call(
                    method_names::CLEANUP_ORPHANED_NODE_MIGRATIONS.to_string(),
                    vec![],
                    NearToken::from_yoctonear(0),
                    Gas::from_tgas(self.config.cleanup_orphaned_node_migrations_tera_gas),
                )
                .detach();
            // Spawn a promise to clean up foreign chain data for non-participants
            Promise::new(env::current_account_id())
                .function_call(
                    method_names::CLEAN_FOREIGN_CHAIN_DATA.to_string(),
                    vec![],
                    NearToken::from_yoctonear(0),
                    Gas::from_tgas(self.config.clean_foreign_chain_data_tera_gas),
                )
                .detach();
            // Spawn a promise to drop verifier-change votes cast by non-participants
            Promise::new(env::current_account_id())
                .function_call(
                    method_names::REMOVE_NON_PARTICIPANT_TEE_VERIFIER_VOTES.to_string(),
                    vec![],
                    NearToken::from_yoctonear(0),
                    Gas::from_tgas(
                        self.config
                            .remove_non_participant_tee_verifier_votes_tera_gas,
                    ),
                )
                .detach();
        }
```

**File:** crates/contract/src/lib.rs (L1363-1374)
```rust
        let valid_votes_count = running_state
            .parameters
            .participants()
            .participants()
            .iter()
            .filter(|(account_id, _, _)| {
                self.proposed_updates
                    .vote_by_participant
                    .get(account_id)
                    .is_some_and(|voted_id| *voted_id == id)
            })
            .count();
```

**File:** crates/contract/src/lib.rs (L1417-1428)
```rust
        let participant = AuthenticatedParticipantId::new(threshold_parameters.participants())?;
        let votes = self.tee_state.vote(code_hash, &participant);

        let tee_upgrade_deadline_duration =
            Duration::from_secs(self.config.tee_upgrade_deadline_duration_seconds);

        // If the vote threshold is met and the new Docker hash is allowed by the TEE's RTMR3,
        // update the state
        if votes >= self.threshold()?.value() {
            self.tee_state
                .whitelist_tee_proposal(code_hash, tee_upgrade_deadline_duration);
        }
```

**File:** crates/contract/src/lib.rs (L1807-1818)
```rust
    pub fn clean_tee_status(&mut self) -> Result<(), Error> {
        log!("clean_tee_status: signer={}", env::signer_account_id());

        let participants = match &self.protocol_state {
            ProtocolContractState::Running(state) => state.parameters.participants(),
            _ => {
                return Err(InvalidState::ProtocolStateNotRunning.into());
            }
        };

        self.tee_state.clean_non_participant_votes(participants);
        Ok(())
```

**File:** crates/contract/src/lib.rs (L1908-1916)
```rust
        let participants = match &self.protocol_state {
            ProtocolContractState::Running(state) => state.parameters.participants(),
            _ => {
                return Err(InvalidState::ProtocolStateNotRunning.into());
            }
        };

        self.tee_verifier_votes.retain(participants);

```
