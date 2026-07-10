### Title
Stale Votes from Removed Participants Counted in TEE Code Hash Threshold Check — (`crates/contract/src/tee/proposal.rs`)

---

### Summary

`CodeHashesVotes::count_votes()` tallies all stored votes for a code hash without filtering to only current participants. After a resharing removes participants, their votes persist in `TeeState` until `clean_tee_status` is explicitly called. The `vote_code_hash` handler compares this unfiltered count against the governance threshold, so stale votes from removed participants count toward whitelisting a new TEE image hash. A single current participant can combine with pre-existing stale votes to cross the threshold and whitelist a malicious code hash — the direct analog of Balancer's `totalSupply()` vs `getActualSupply()` error.

---

### Finding Description

**Root cause — wrong metric in `count_votes`**

`CodeHashesVotes::vote()` inserts the new vote and then calls `count_votes`, which iterates over every value in `proposal_by_account` and counts matches, with no participant-membership filter: [1](#0-0) 

```rust
fn count_votes(&self, proposal: &NodeImageHash) -> u64 {
    self.proposal_by_account
        .values()
        .filter(|&prop| prop == proposal)
        .count() as u64
}
```

The correct metric — votes from *current* participants only — is already implemented in `get_remaining_votes`, but is never used for the threshold check: [2](#0-1) 

**Stale votes survive resharing**

`TeeState` (and its embedded `CodeHashesVotes`) is not reset when the contract transitions through a resharing. Cleanup requires an explicit call to `clean_tee_status` / `clean_non_participant_votes`. The test that documents this scenario explicitly notes the cleanup is *not* automatic: [3](#0-2) 

**Threshold check uses the unfiltered count**

`vote_code_hash` compares the raw return value of `tee_state.vote()` — which is the unfiltered `count_votes` result — against the governance threshold: [4](#0-3) 

```rust
let votes = self.tee_state.vote(code_hash, &participant);
if votes >= self.threshold()?.value() {
    self.tee_state.whitelist_tee_proposal(code_hash, tee_upgrade_deadline_duration);
}
```

The same pattern exists in `MeasurementVotes::count_votes` and `LauncherHashVotes::count_votes`: [5](#0-4) 

---

### Impact Explanation

Whitelisting a malicious Docker image hash means nodes running that image can submit valid TEE attestations. An accepted attestation is the prerequisite for a node to be admitted as a participant in the MPC network. A participant node running adversarial code can leak key shares or produce unauthorized threshold signatures. This maps to the **Critical** allowed impact: *"Bypass of threshold-signature requirements or unauthorized access to MPC key shares, signing capability, or secret material that materially enables forgery or secret recovery."*

---

### Likelihood Explanation

The attack requires:
1. At least one participant who will later be removed to pre-vote for the target hash.
2. A resharing that removes those participants.
3. `clean_tee_status` to not be called before the attacker's vote is cast.

Condition 3 is realistic: `clean_tee_status` is a separate, permissioned call with no on-chain enforcement that it runs immediately after resharing. Participants being removed due to TEE expiry (the `verify_tee` path) is a normal operational event, not a sign of malice, so their pre-existing votes for a hash are not suspicious. A single colluding new participant can then trigger the threshold.

---

### Recommendation

Replace the unfiltered `count_votes` call inside `CodeHashesVotes::vote`, `MeasurementVotes::vote`, and `LauncherHashVotes::vote` with a participant-filtered count, passing the current `Participants` set at call time — exactly as `get_remaining_votes` already does. Alternatively, call `clean_non_participant_votes` atomically inside the resharing completion logic so stale votes can never accumulate.

---

### Proof of Concept

```
Setup: N=5 participants {P1,P2,P3,P4,P5}, governance threshold T=3.

Step 1: P1 and P2 vote for malicious hash H.
        count_votes(H) = 2  →  below threshold, H not whitelisted.

Step 2: Resharing removes P1 and P2 (e.g., TEE attestation expired).
        New participant set: {P3, P4, P5}.
        CodeHashesVotes still contains P1's and P2's entries for H.

Step 3: clean_tee_status is not called (or is delayed).

Step 4: P3 (a colluding new participant) calls vote_code_hash(H).
        CodeHashesVotes::vote() inserts P3's entry, then calls count_votes(H).
        count_votes iterates ALL entries: P1→H, P2→H, P3→H → returns 3.
        3 >= threshold(3)  →  whitelist_tee_proposal(H) executes.

Step 5: Attacker deploys a node running image H.
        Node submits TEE attestation; contract accepts it (H is whitelisted).
        Attacker node is eligible to be voted in as a participant.
        Once admitted, it participates in threshold signing with access to key shares.
```

The unfiltered `count_votes` is the necessary vulnerable step: without it, P3's single vote (count = 1) would not reach threshold 3.

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

**File:** crates/contract/src/tee/tee_state.rs (L1496-1544)
```rust
    /// Stale CodeHashesVotes entries from removed participants must not count toward
    /// quorum after resharing.
    ///
    /// Scenario (N=5, T=3):
    /// 1. P1 and P2 vote for malicious hash before resharing.
    /// 2. Resharing removes P1 and P2. New set: {P3, P4, P5}.
    /// 3. clean_non_participant_votes removes stale votes.
    /// 4. P3 votes for the same hash — only 1 vote, not 3.
    #[test]
    fn test_clean_non_participant_votes_removes_stale_votes() {
        // Build 5 participants
        let mut all_participants = Participants::new();
        let mut account_ids = Vec::new();
        for i in 0..5 {
            let (account_id, info) = gen_participant(i);
            account_ids.push(account_id.clone());
            all_participants.insert(account_id, info).unwrap();
        }

        let mut tee_state = TeeState::default();

        // P0 and P1 vote for a malicious hash before resharing
        let malicious_hash = NodeImageHash::from([0xAA; 32]);
        for account_id in &account_ids[0..2] {
            let mut ctx = VMContextBuilder::new();
            ctx.signer_account_id(account_id.clone());
            testing_env!(ctx.build());
            let auth_id = AuthenticatedParticipantId::new(&all_participants).unwrap();
            tee_state.votes.vote(malicious_hash, &auth_id);
        }
        assert_eq!(tee_state.votes.proposal_by_account.len(), 2);

        // Resharing removes P0 and P1. New participant set: {P2, P3, P4}.
        let new_participants = all_participants.subset(2..5);

        // Clean non-participants (as done by CLEAN_TEE_STATUS after resharing)
        tee_state.clean_non_participant_votes(&new_participants);

        // Stale votes must be removed
        assert_eq!(tee_state.votes.proposal_by_account.len(), 0);

        // P2 votes for the same malicious hash — should be only 1 vote, not 3
        let p2_account = &account_ids[2];
        let mut ctx = VMContextBuilder::new();
        ctx.signer_account_id(p2_account.clone());
        testing_env!(ctx.build());
        let auth_id = AuthenticatedParticipantId::new(&new_participants).unwrap();
        let vote_count = tee_state.votes.vote(malicious_hash, &auth_id);
        assert_eq!(vote_count, 1, "Only the fresh vote from P2 should count");
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

**File:** crates/contract/src/tee/measurements.rs (L57-66)
```rust
    /// Counts the total number of participants who have voted for the given action.
    fn count_votes(&self, action: &MeasurementVoteAction) -> u64 {
        u64::try_from(
            self.vote_by_account
                .values()
                .filter(|a| *a == action)
                .count(),
        )
        .expect("participant count should not overflow u64")
    }
```
