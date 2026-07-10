### Title
Stale TEE Vote Counts After Resharing Allow Governance Threshold Bypass in `vote_code_hash` and Related Functions - (File: `crates/contract/src/lib.rs`)

### Summary
After a resharing event removes participants, their TEE-related votes (code hashes, launcher hashes, OS measurements) are cleaned up via a **detached promise** that can silently fail. Unlike `vote_update`, which defensively re-filters votes against the live participant set to guard against exactly this failure, the TEE voting functions (`vote_code_hash`, `vote_add_launcher_hash`, `vote_add_os_measurement`, `vote_remove_launcher_hash`, `vote_remove_os_measurement`) rely entirely on the cleanup promise. If the cleanup fails, stale votes from removed participants inflate the raw vote count, allowing TEE proposals to cross the governance threshold with fewer current participants than required.

### Finding Description

**Root cause — asymmetric defensive filtering**

`vote_update` explicitly re-counts only votes cast by *current* participants, with a comment that names the exact failure mode:

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
``` [1](#0-0) 

The TEE voting functions do **not** apply this guard. They call `self.tee_state.vote(...)` and compare the raw returned count directly to the threshold:

```rust
let votes = self.tee_state.vote(code_hash, &participant);
if votes >= self.threshold()?.value() {
    self.tee_state.whitelist_tee_proposal(code_hash, tee_upgrade_deadline_duration);
}
``` [2](#0-1) 

The same pattern applies to `vote_add_launcher_hash` and `vote_add_os_measurement`. [3](#0-2) [4](#0-3) 

**Stale-vote source — detached cleanup promise**

After a successful resharing, `vote_reshared` spawns `clean_tee_status` as a **detached** promise:

```rust
Promise::new(env::current_account_id())
    .function_call(
        method_names::CLEAN_TEE_STATUS.to_string(),
        ...
    )
    .detach();
``` [5](#0-4) 

`.detach()` means the contract never observes whether the cleanup succeeded. If it fails (e.g., gas exhaustion when many TEE entries exist), removed participants' votes remain in `tee_state` indefinitely.

**Concrete stale-state scenario (analog to `totalWeight` inflation)**

| Step | State |
|------|-------|
| Participants `{A,B,C,D,E}`, governance threshold = 3 | — |
| A votes for TEE code hash `X` | 1 vote for X |
| Resharing removes A → `{B,C,D,E}`, threshold = 3 | `clean_tee_status` fails; A's vote persists |
| B votes for `X` | raw count = 2 (A stale + B) |
| C votes for `X` | raw count = 3 ≥ threshold → **X is whitelisted** |

Only 2 current participants (B, C) voted, but the threshold of 3 appears satisfied because A's stale vote is still counted. This is structurally identical to the `totalWeight` bug: a weight that should have been removed when the participant left is still included in the aggregate used for the threshold check.

### Impact Explanation

A TEE code hash, launcher hash, or OS measurement can be whitelisted with fewer current participants than the governance threshold requires. This breaks the participant-state accounting invariant that governs which node software is trusted. A node running a code hash whitelisted through this bypass can submit a valid-looking attestation, be voted in as a participant by the remaining honest majority, and then participate in threshold signing. If the whitelisted code is adversarial, it could exfiltrate key-share material or produce biased signatures, enabling unauthorized transaction execution. This matches the **Medium** allowed impact: participant-state manipulation that breaks production safety/accounting invariants.

### Likelihood Explanation

The cleanup promise failure is not hypothetical — the `vote_update` defensive comment explicitly acknowledges it as a realistic scenario. Gas exhaustion during `clean_tee_status` is plausible when the TEE attestation map is large (many prospective participants submitted info). An adversary who anticipates a resharing can pre-vote for a target code hash before being removed, then rely on the cleanup failure to preserve their stale vote. No privileged access is required beyond being a current participant at the time of the pre-vote.

### Recommendation

Apply the same defensive re-filtering used in `vote_update` to all TEE voting functions. Before comparing the vote count to the threshold, iterate over `threshold_parameters.participants()` and count only those whose stored vote matches the current proposal — exactly as done for contract upgrades. This makes the threshold check correct regardless of whether the cleanup promise succeeds.

### Proof of Concept

```
// Precondition: 5 participants {A,B,C,D,E}, governance threshold = 3.
// 1. A calls vote_code_hash(X)  → tee_state records A→X, returns count=1
// 2. Resharing removes A; clean_tee_status detached promise fails (e.g. gas limit hit).
//    A's vote for X remains in tee_state.
// 3. B calls vote_code_hash(X)  → tee_state records B→X, returns count=2 (A stale + B)
// 4. C calls vote_code_hash(X)  → tee_state records C→X, returns count=3 ≥ threshold(3)
//    → whitelist_tee_proposal(X) executes.
// Result: X whitelisted with only 2 current-participant votes (B, C),
//         not the required 3, because A's stale vote was never purged.
```

The fix is to replace the raw `votes` count with a filtered count analogous to `vote_update`:

```rust
// Instead of:
let votes = self.tee_state.vote(code_hash, &participant);
if votes >= self.threshold()?.value() { ... }

// Use:
self.tee_state.vote(code_hash, &participant); // record the vote
let valid_votes = threshold_parameters.participants().participants().iter()
    .filter(|(_, id, _)| self.tee_state.has_voted_for(id, &code_hash))
    .count() as u64;
if valid_votes >= self.threshold()?.value() { ... }
```

### Citations

**File:** crates/contract/src/lib.rs (L1186-1194)
```rust
            Promise::new(env::current_account_id())
                .function_call(
                    method_names::CLEAN_TEE_STATUS.to_string(),
                    vec![],
                    NearToken::from_yoctonear(0),
                    Gas::from_tgas(self.config.clean_tee_status_tera_gas),
                )
                .detach();
            // Spawn a bounded sweep over stored attestations to prune invalid / expired entries.
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

**File:** crates/contract/src/lib.rs (L1418-1428)
```rust
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

**File:** crates/contract/src/lib.rs (L1452-1464)
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

        Ok(())
```

**File:** crates/contract/src/lib.rs (L1514-1520)
```rust
        let votes = self.tee_state.vote_measurement(action, &participant);

        if votes >= self.threshold()?.value() {
            let added = self.tee_state.add_measurement(measurement);
            log!("OS measurement add result: {}", added);
        }

```
