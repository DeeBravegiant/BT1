### Title
Stale Non-Participant Votes Counted in `CodeHashesVotes` Threshold Check, Enabling Unauthorized MPC Image Hash Whitelisting — (File: `crates/contract/src/tee/proposal.rs`)

---

### Summary

`CodeHashesVotes.count_votes()` tallies **all** stored votes for a proposal without filtering by current participants. Between resharing completion and the asynchronous `clean_tee_status` callback, stale votes from removed participants remain in storage. A single remaining participant's vote can combine with those stale votes to meet the governance threshold, causing unauthorized MPC image-hash whitelisting without the required number of current-participant authorizations.

---

### Finding Description

**Root cause — wrong counting method in `CodeHashesVotes`**

In `crates/contract/src/tee/proposal.rs`, `count_votes()` iterates over every entry in `proposal_by_account` and counts matches, with no participant-membership filter:

```rust
// crates/contract/src/tee/proposal.rs  lines 47-52
fn count_votes(&self, proposal: &NodeImageHash) -> u64 {
    self.proposal_by_account
        .values()
        .filter(|&prop| prop == proposal)
        .count() as u64
}
```

`vote()` calls this and returns the raw total to the caller, which then compares it against the governance threshold:

```rust
// crates/contract/src/tee/proposal.rs  lines 29-43
pub fn vote(&mut self, proposal: NodeImageHash, participant: &AuthenticatedParticipantId) -> u64 {
    ...
    let total = self.count_votes(&proposal);
    total   // caller checks: if total >= threshold { whitelist }
}
```

**Contrast with the correct pattern used elsewhere**

`TeeVerifierVotes.vote()` in `crates/contract/src/tee/verifier_votes.rs` explicitly filters stale entries at count time:

```rust
// crates/contract/src/tee/verifier_votes.rs  lines 74-77
let count_usize = {
    let voter_set = self.pending.vote(participant, proposal_hash);
    voter_set.count_for(|p| participants.is_participant_given_participant_id(&p.get()))
};
```

`ProviderVotes.vote()` in `crates/contract/src/foreign_chain_rpc.rs` applies the same guard:

```rust
// crates/contract/src/foreign_chain_rpc.rs  lines 183-188
let count_usize = {
    let voter_set = self.pending.vote((participant, chain), hash);
    voter_set.count_for(|(p, c)| {
        *c == chain && participants.is_participant_given_participant_id(&p.get())
    })
};
```

`CodeHashesVotes` (and identically `LauncherHashVotes`) is the only vote-counting path that omits this filter.

**The asynchronous cleanup window**

The stale-vote pruner is `clean_tee_status`, declared `#[private]` in `crates/contract/src/lib.rs` (line 1805), meaning it executes as a NEAR promise callback — in a subsequent receipt, not atomically with the resharing transaction:

```rust
// crates/contract/src/lib.rs  lines 1805-1818
#[private]
#[handle_result]
pub fn clean_tee_status(&mut self) -> Result<(), Error> {
    ...
    self.tee_state.clean_non_participant_votes(participants);
    Ok(())
}
```

Between the block that finalizes resharing and the block that executes `clean_tee_status`, stale votes from removed participants remain live in `proposal_by_account`. Any `vote_mpc_image_hash` call that lands in this window uses the inflated count.

The project's own test documents the intended invariant — that after cleanup only fresh votes count — but does not test the pre-cleanup window:

```rust
// crates/contract/src/tee/tee_state.rs  lines 1537-1544
// P2 votes for the same malicious hash — should be only 1 vote, not 3
let vote_count = tee_state.votes.vote(malicious_hash, &auth_id);
assert_eq!(vote_count, 1, "Only the fresh vote from P2 should count");
```

---

### Impact Explanation

Whitelisting an unauthorized MPC image hash allows a malicious node binary to pass TEE attestation and join the signing network. Once inside, the binary has access to in-TEE key-share material and can sign arbitrary payloads or exfiltrate shares. This satisfies:

> *Critical. Bypass of threshold-signature requirements or unauthorized access to MPC key shares, signing capability, or secret material that materially enables forgery or secret recovery.*

The same flaw exists in `LauncherHashVotes.count_votes()` (lines 112–120 of the same file), which governs launcher image whitelisting and carries equivalent impact.

---

### Likelihood Explanation

The attack requires:

1. One or more Byzantine participants (strictly below the signing threshold, so no collusion disqualifier applies) vote for a malicious image hash before a resharing event.
2. Those participants are removed by the resharing (legitimately or by voluntarily not re-joining).
3. A single surviving participant — honest or Byzantine — casts a vote for the same hash in the one-block window before `clean_tee_status` executes.

Step 3 is the only timing constraint. Because NEAR block times are ~1 second and the cleanup receipt is scheduled immediately after resharing, the window is narrow but deterministic and observable on-chain. A participant watching the chain can submit the vote in the very next block after resharing finalizes. No privileged access, no TEE attack, and no threshold-level collusion is required.

---

### Recommendation

Apply the same participant-membership filter used by `TeeVerifierVotes` and `ProviderVotes` inside `CodeHashesVotes.count_votes()`. The method signature should accept the current `Participants` set and filter before counting:

```rust
fn count_votes(&self, proposal: &NodeImageHash, participants: &Participants) -> u64 {
    self.proposal_by_account
        .iter()
        .filter(|(participant_id, prop)| {
            *prop == proposal
                && participants.is_participant_given_participant_id(&participant_id.get())
        })
        .count() as u64
}
```

Apply the identical fix to `LauncherHashVotes.count_votes()`. This eliminates the dependency on the asynchronous cleanup window entirely.

---

### Proof of Concept

**Setup**: N = 5 participants, governance threshold T = 3.

1. P1 and P2 (both current participants) call `vote_mpc_image_hash(malicious_hash)`. Two entries are written to `proposal_by_account`. Count = 2 < 3; hash not yet whitelisted.
2. A resharing governance vote (requiring T = 3 honest votes) removes P1 and P2. New participant set: {P3, P4, P5}, new threshold = 3. The resharing receipt finalizes; `clean_tee_status` is scheduled as a subsequent callback receipt.
3. **Before `clean_tee_status` executes** (same or next block), P3 calls `vote_mpc_image_hash(malicious_hash)`.
4. `CodeHashesVotes.vote()` inserts P3's entry and calls `count_votes(malicious_hash)`.
5. `count_votes` iterates `proposal_by_account` and finds three matching entries: P1 (stale), P2 (stale), P3 (fresh). Returns 3.
6. Caller checks `3 >= 3` → **threshold met** → `malicious_hash` is added to the allowed image-hash list.
7. A node binary built with `malicious_hash` now passes TEE attestation, joins the MPC network, and has in-enclave access to key-share material. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** crates/contract/src/tee/proposal.rs (L29-43)
```rust
    pub fn vote(
        &mut self,
        proposal: NodeImageHash,
        participant: &AuthenticatedParticipantId,
    ) -> u64 {
        if self
            .proposal_by_account
            .insert(participant.clone(), proposal)
            .is_some()
        {
            log!("removed old vote for signer");
        }
        let total = self.count_votes(&proposal);
        log!("total votes for proposal: {}", total);
        total
```

**File:** crates/contract/src/tee/proposal.rs (L47-52)
```rust
    fn count_votes(&self, proposal: &NodeImageHash) -> u64 {
        self.proposal_by_account
            .values()
            .filter(|&prop| prop == proposal)
            .count() as u64
    }
```

**File:** crates/contract/src/tee/proposal.rs (L112-120)
```rust
    fn count_votes(&self, action: &LauncherVoteAction) -> u64 {
        u64::try_from(
            self.vote_by_account
                .values()
                .filter(|a| *a == action)
                .count(),
        )
        .expect("participant count should not overflow u64")
    }
```

**File:** crates/contract/src/tee/verifier_votes.rs (L74-77)
```rust
        let count_usize = {
            let voter_set = self.pending.vote(participant, proposal_hash);
            voter_set.count_for(|p| participants.is_participant_given_participant_id(&p.get()))
        };
```

**File:** crates/contract/src/foreign_chain_rpc.rs (L183-188)
```rust
        let count_usize = {
            let voter_set = self.pending.vote((participant, chain), hash);
            voter_set.count_for(|(p, c)| {
                *c == chain && participants.is_participant_given_participant_id(&p.get())
            })
        };
```

**File:** crates/contract/src/lib.rs (L1805-1818)
```rust
    #[private]
    #[handle_result]
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

**File:** crates/contract/src/tee/tee_state.rs (L1537-1544)
```rust
        // P2 votes for the same malicious hash — should be only 1 vote, not 3
        let p2_account = &account_ids[2];
        let mut ctx = VMContextBuilder::new();
        ctx.signer_account_id(p2_account.clone());
        testing_env!(ctx.build());
        let auth_id = AuthenticatedParticipantId::new(&new_participants).unwrap();
        let vote_count = tee_state.votes.vote(malicious_hash, &auth_id);
        assert_eq!(vote_count, 1, "Only the fresh vote from P2 should count");
```
