### Title
Stale TEE Votes from Removed Participants Bypass Threshold in `vote_code_hash` / `vote_add_launcher_hash` / `vote_add_os_measurement` - (File: crates/contract/src/tee/proposal.rs, crates/contract/src/lib.rs)

---

### Summary

After a resharing event removes participants, their TEE governance votes (`vote_code_hash`, `vote_add_launcher_hash`, `vote_add_os_measurement`) persist in contract storage and are counted toward the threshold without filtering. The post-resharing cleanup (`clean_tee_status`) is a **detached promise** that can fail silently. Unlike `vote_update`, which explicitly re-filters votes against the current participant set at threshold-check time, the TEE voting functions count all stored votes unconditionally. This allows a malicious Docker image hash to be whitelisted with fewer current-participant votes than the governance threshold requires.

---

### Finding Description

The `CodeHashesVotes::count_votes` function counts every entry in `proposal_by_account` for a given hash, with no filter for whether the voter is still a current participant: [1](#0-0) 

`vote_code_hash` calls this raw count and compares it directly to the threshold: [2](#0-1) 

The same unfiltered pattern applies to `vote_add_launcher_hash` and `vote_add_os_measurement`: [3](#0-2) [4](#0-3) 

After resharing completes, `vote_reshared` spawns `clean_tee_status` as a **detached** promise: [5](#0-4) 

If this detached promise fails (e.g., insufficient gas allocation, NEAR runtime error), stale votes from removed participants remain in `TeeState.votes`, `TeeState.launcher_votes`, and `TeeState.measurement_votes`.

By contrast, `vote_update` explicitly re-filters stored votes against the current participant set at threshold-check time and its own code comment acknowledges the cleanup can fail: [6](#0-5) 

The `get_remaining_votes` helper exists on `CodeHashesVotes` and is used by `clean_non_participant_votes`, but is **never called** at threshold-check time inside `vote_code_hash`: [7](#0-6) 

---

### Impact Explanation

**Scenario (threshold = 3, initial participants = {P1, P2, P3, P4, P5}):**

1. P1 (attacker-controlled) votes for `malicious_hash` via `vote_code_hash`.
2. Resharing removes P1; new set = {P2, P3, P4, P5}, threshold = 3.
3. `clean_tee_status` detached promise fails; P1's stale vote remains.
4. P2 and P3 vote for `malicious_hash` → `count_votes` returns 3 (P1 stale + P2 + P3) ≥ threshold.
5. `whitelist_tee_proposal` is called; `malicious_hash` is added to `allowed_docker_image_hashes`.

A whitelisted malicious Docker image hash allows nodes running adversarial code to submit valid TEE attestations and join the MPC network as attested participants. If enough such nodes accumulate (≥ reconstruction threshold), they can participate in threshold signing, potentially enabling unauthorized signature issuance or key-share exposure.

This maps to: **Medium** — participant-state manipulation that breaks production safety invariants (TEE attestation gating), with a path to Critical if the malicious nodes reach signing threshold.

---

### Likelihood Explanation

- Resharing events that change the participant set are a normal operational occurrence.
- Detached NEAR promises can fail if the gas budget (`clean_tee_status_tera_gas`) is misconfigured or if the runtime encounters an error; the parent transaction succeeds regardless.
- The attacker only needs to have been a participant at some point and to have voted for the target hash before removal — no threshold-or-above collusion is required.
- The codebase itself acknowledges this risk for `vote_update` but does not apply the same defense to TEE voting functions.

---

### Recommendation

Apply the same at-call-time participant filtering used in `vote_update` to all TEE governance voting functions. Before comparing the vote count to the threshold, filter `proposal_by_account` to only count entries whose `AuthenticatedParticipantId` belongs to the current participant set (using `is_participant_given_participant_id`), mirroring the `get_remaining_votes` logic already present on `CodeHashesVotes`.

Alternatively, make the threshold check in `vote_code_hash`, `vote_add_launcher_hash`, and `vote_add_os_measurement` call `get_remaining_votes` (or an equivalent inline filter) before counting, so stale votes from removed participants are excluded regardless of whether the cleanup promise succeeded.

---

### Proof of Concept

```
State: 5 participants {P1..P5}, threshold = 3.

Step 1: P1 calls vote_code_hash(malicious_hash).
        proposal_by_account = {P1 → malicious_hash}
        count_votes = 1 < 3 → not whitelisted.

Step 2: Resharing removes P1. New set = {P2..P5}, threshold = 3.
        clean_tee_status detached promise FAILS.
        proposal_by_account still = {P1 → malicious_hash}  ← stale

Step 3: P2 calls vote_code_hash(malicious_hash).
        proposal_by_account = {P1(stale) → malicious_hash, P2 → malicious_hash}
        count_votes = 2 < 3 → not whitelisted.

Step 4: P3 calls vote_code_hash(malicious_hash).
        proposal_by_account = {P1(stale), P2, P3} → malicious_hash
        count_votes = 3 >= threshold(3) → whitelist_tee_proposal called!

Result: malicious_hash whitelisted with only 2 current-participant votes (P2, P3)
        instead of the required 3.
``` [1](#0-0) [8](#0-7) [5](#0-4)

### Citations

**File:** crates/contract/src/tee/proposal.rs (L46-52)
```rust
    /// Counts the total number of participants who have voted for the given code hash.
    fn count_votes(&self, proposal: &NodeImageHash) -> u64 {
        self.proposal_by_account
            .values()
            .filter(|&prop| prop == proposal)
            .count() as u64
    }
```

**File:** crates/contract/src/tee/proposal.rs (L59-72)
```rust
    /// Returns a new `CodeHashesVotes` containing only votes from current participants.
    pub fn get_remaining_votes(&self, participants: &Participants) -> Self {
        let remaining = self
            .proposal_by_account
            .iter()
            .filter(|(participant_id, _)| {
                participants.is_participant_given_participant_id(&participant_id.get())
            })
            .map(|(participant_id, vote)| (participant_id.clone(), *vote))
            .collect();
        CodeHashesVotes {
            proposal_by_account: remaining,
        }
    }
```

**File:** crates/contract/src/lib.rs (L1185-1193)
```rust
            // Spawn a promise to drop votes cast by non-participants.
            Promise::new(env::current_account_id())
                .function_call(
                    method_names::CLEAN_TEE_STATUS.to_string(),
                    vec![],
                    NearToken::from_yoctonear(0),
                    Gas::from_tgas(self.config.clean_tee_status_tera_gas),
                )
                .detach();
```

**File:** crates/contract/src/lib.rs (L1361-1374)
```rust
        // Filter votes to only count current participants voting for this specific update.
        // This ensures correctness even if the cleanup promise in MpcContract::vote_reshared() fails.
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

**File:** crates/contract/src/lib.rs (L1415-1428)
```rust
        let threshold_parameters = self.protocol_state.threshold_parameters_or_panic();

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

**File:** crates/contract/src/lib.rs (L1452-1462)
```rust
        let votes = self.tee_state.vote_launcher(action, &participant);

        let tee_upgrade_deadline_duration =
            Duration::from_secs(self.config.tee_upgrade_deadline_duration_seconds);

        if votes >= self.threshold()?.value() {
            let added = self
                .tee_state
                .add_launcher_image(launcher_hash, tee_upgrade_deadline_duration);
            log!("launcher hash add result: {}", added);
        }
```

**File:** crates/contract/src/lib.rs (L1514-1519)
```rust
        let votes = self.tee_state.vote_measurement(action, &participant);

        if votes >= self.threshold()?.value() {
            let added = self.tee_state.add_measurement(measurement);
            log!("OS measurement add result: {}", added);
        }
```
