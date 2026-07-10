The bug is real and confirmed. Here is the full analysis:

---

### Title
Stale `cancel_votes` Persist Across Domain Transitions, Allowing Sub-Threshold Cancellation of Key Generation — (`crates/contract/src/state/initializing.rs`)

### Summary
`InitializingContractState.cancel_votes` is never cleared when `vote_pk` advances key generation to the next domain. Cancel votes cast during an earlier domain silently carry over and count toward the threshold for all subsequent domains in the same session.

### Finding Description

`cancel_votes` is a `BTreeSet<AuthenticatedParticipantId>` stored directly on `InitializingContractState`. [1](#0-0) 

When `vote_pk` collects enough votes to complete a domain, it replaces `self.generating_key` with a fresh `KeyEvent` for the next domain, but **does not touch `self.cancel_votes`**: [2](#0-1) 

`vote_cancel` inserts the caller into `cancel_votes` and checks the length against threshold. Because `BTreeSet::insert` returns `false` for a duplicate, a participant who already voted cancel during a prior domain cannot add a second entry — but their first entry is still present and still counted: [3](#0-2) 

The `next_domain_id` guard only binds the vote to the overall session (the fixed maximum domain ID + 1), not to the specific domain currently being generated, so it provides no protection against cross-domain vote reuse: [4](#0-3) 

`cancel_votes` is correctly initialized to `BTreeSet::new()` only at session creation: [5](#0-4) 

### Impact Explanation

**Impact: Medium** — this does not match the Critical scope claimed in the question. Triggering `vote_cancel` does not expose key material, enable forgery, or issue an unauthorized signature. Its effect is to revert to `RunningContractState` and **permanently delete** the domain IDs that had not yet received a generated key.

The broken invariant is: *a threshold number of participants must actively vote to cancel the current key-generation session.* With stale votes carrying over, T−1 participants who voted cancel during domain 0 (without reaching threshold) leave their votes counted for domain 1. A single additional participant voting cancel for domain 1 then triggers cancellation — a threshold bypass that permanently removes planned domains from the registry without the required quorum.

This falls under **Medium** — participant-state and contract execution-flow manipulation that breaks a production safety invariant (the cancel-vote threshold) without requiring network-level DoS or operator misconfiguration.

### Likelihood Explanation

Reachable by any single Byzantine participant (or coordinated group below threshold) who participates honestly in `vote_pk` for one domain and then calls `vote_cancel` for the next. No special privileges are required beyond being a registered participant.

### Recommendation

Clear `cancel_votes` whenever `generating_key` advances to a new domain inside `vote_pk`:

```rust
// in vote_pk, after pushing to generated_keys and before creating the new KeyEvent:
self.cancel_votes.clear();
self.generating_key = KeyEvent::new(...);
```

Alternatively, bind each cancel vote to the specific domain being generated (e.g., include the current `domain_id` in the vote key) so votes from prior domains are structurally ineligible for later ones.

### Proof of Concept

1. Create an `InitializingContractState` with 2+ domains and threshold T.
2. Have T−1 participants call `vote_cancel` during domain 0 — threshold not reached, `cancel_votes.len() == T−1`.
3. Have all participants call `vote_pk` for domain 0 until it completes; `generating_key` advances to domain 1, `cancel_votes` still holds T−1 entries.
4. Have one new participant call `vote_cancel` for domain 1 — `cancel_votes.len()` reaches T, cancellation fires.
5. Assert that the returned `RunningContractState` has only the domain 0 key, and that domain 1 is permanently gone — achieved with only 1 active cancel vote for domain 1 instead of the required T.

---

**Verdict: Real vulnerability, Medium impact.** The Critical scope claim (forgery / key-material access) is not satisfied; the actual harm is a sub-threshold governance bypass that permanently deletes planned domains.

### Citations

**File:** crates/contract/src/state/initializing.rs (L41-43)
```rust
    /// Votes that have been cast to cancel the key generation.
    pub cancel_votes: BTreeSet<AuthenticatedParticipantId>,
}
```

**File:** crates/contract/src/state/initializing.rs (L86-91)
```rust
            if let Some(next_domain) = self.domains.get_domain_by_index(self.generated_keys.len()) {
                self.generating_key = KeyEvent::new(
                    self.epoch_id,
                    next_domain.clone(),
                    self.generating_key.proposed_parameters().clone(),
                );
```

**File:** crates/contract/src/state/initializing.rs (L121-123)
```rust
        if next_domain_id != self.domains.next_domain_id() {
            return Err(InvalidParameters::NextDomainIdMismatch.into());
        }
```

**File:** crates/contract/src/state/initializing.rs (L132-141)
```rust
        if self.cancel_votes.insert(participant) && self.cancel_votes.len() >= required_threshold {
            let mut domains = self.domains.clone();
            domains.retain_domains(self.generated_keys.len());
            return Ok(Some(RunningContractState::new(
                domains,
                Keyset::new(self.epoch_id, self.generated_keys.clone()),
                self.generating_key.proposed_parameters().clone(),
                AddDomainsVotes::default(),
            )));
        }
```

**File:** crates/contract/src/state/running.rs (L248-249)
```rust
                cancel_votes: BTreeSet::new(),
            }))
```
