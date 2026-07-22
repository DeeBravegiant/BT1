### Title
Malicious Peer Pre-Occupies Future-Height Proposal Cache Slot, Permanently Silencing Legitimate Proposals - (File: `crates/apollo_consensus/src/manager.rs`)

---

### Summary

`ConsensusCache::cache_future_proposal` uses `or_insert`, so the **first** proposal received for a given `(height, round)` pair permanently occupies that slot. The proposer check in `handle_proposal` only verifies that the `init.proposer` *field* matches the deterministically-computed expected proposer — it does not authenticate the network sender. Any peer can therefore craft a `ProposalInit` with the correct `proposer` field but garbage content, pre-occupy the cache slot for a future height/round, and cause the legitimate proposal from the real proposer to be silently dropped when that height becomes current. The code itself acknowledges this in a TODO comment at the exact site.

---

### Finding Description

**Root cause — `or_insert` in `cache_future_proposal`**

```rust
// crates/apollo_consensus/src/manager.rs  lines 349-358
fn cache_future_proposal(
    &mut self,
    init: ProposalInit,
    content_receiver: mpsc::Receiver<ContextT::ProposalPart>,
) {
    self.future_proposals_cache
        .entry(init.height)
        .or_default()
        .entry(init.round)
        .or_insert((init, content_receiver));   // ← first writer wins; all later arrivals are silently dropped
}
``` [1](#0-0) 

**Proposer check is field-only, not sender-authenticated**

In `handle_proposal`, before caching a future-height proposal, the code checks:

```rust
if proposer != init.proposer { … return Ok(VecDeque::new()); }
```

`proposer` is the result of `get_proposer_for_height(committee_provider, init.height, init.round)` — a deterministic, publicly-computable value. `init.proposer` is just a field in the incoming protobuf message. Any peer can set that field to the correct value. There is no signature or transport-layer identity check on the proposal stream. [2](#0-1) 

**The code acknowledges the attack**

```rust
// Note: new proposals with the same height/round will be ignored.
//
// TODO(matan): This only work for trusted peers. In the case of
// possibly malicious peers this is a
// possible DoS attack (malicious
// users can insert invalid/bad/malicious proposals before
// "good" nodes can propose).
//
// When moving to version 1.0 make sure this is addressed.
self.cache.cache_future_proposal(init, content_receiver);
``` [3](#0-2) 

**Cached proposals are replayed without re-checking sender identity**

When the height becomes current, `process_start_height` replays every cached proposal directly into `handle_proposal_known_proposal_init` — no second proposer-identity check occurs:

```rust
let cached_proposals = self.cache.get_current_height_proposals(height);
for (init, content_receiver) in cached_proposals {
    let new_requests =
        self.handle_proposal_known_proposal_init(height, shc, init, content_receiver).await;
    …
}
``` [4](#0-3) 

**Duplicate-round guard in `SingleHeightConsensus` seals the fate of the legitimate proposal**

Once the malicious proposal is replayed, `shc.handle_proposal` inserts the round into `pending_validation_rounds`. When the legitimate proposal from the real proposer arrives moments later, the guard fires and the legitimate proposal is permanently discarded:

```rust
if self.state_machine.has_proposal_for_round(init.round)
    || self.pending_validation_rounds.contains(&init.round)
{
    warn!("Round {} already handled a proposal, ignoring", init.round);
    return VecDeque::new();
}
``` [5](#0-4) 

---

### Impact Explanation

The validator node calls `validate_proposal` with the malicious content receiver. The receiver contains garbage or is immediately closed, so validation fails or times out. The node emits a nil prevote for round 0. The attacker repeats the same trick for round 1, 2, … (the future-height cache accepts up to `future_height_round_limit` rounds). The targeted node never validates a legitimate proposal and never reaches a decision for any height the attacker pre-poisons. This is a targeted consensus-liveness attack: the affected validator is permanently silenced for every height within the attacker's pre-cache window, degrading or halting block production if enough validators are targeted.

This maps to the **proposal validation path** audit pivot (`handle_proposal` → `cache_future_proposal` → `process_start_height` → `validate_proposal`) and to the **High** impact category: a valid proposal is rejected before sequencing because the admission gate (`cache_future_proposal`) accepts the first-arriving message without authenticating the sender.

---

### Likelihood Explanation

- The proposer schedule is deterministic and publicly derivable from the committee and block hash — any connected peer can compute it.
- The attack requires sending a single well-formed `ProposalInit` protobuf message per height/round; no stake, no key material, no privileged access.
- The `future_height_limit` configuration (default 10) means an attacker can pre-poison up to 10 future heights in a single burst.
- The code comment explicitly flags this as an unresolved known issue, confirming the attack surface is live in production.

---

### Recommendation

1. **Replace `or_insert` with an authenticated overwrite policy**: only accept a future-height proposal if it is accompanied by a verifiable signature from the expected proposer (e.g., the same BLS/ECDSA key used for votes). Until signatures are available, drop any future-height proposal whose sender peer-ID does not match the expected proposer's registered network identity.

2. **Short-term mitigation**: change `or_insert` to always overwrite (`insert`), so the *last* received proposal wins. This does not fully fix the problem but prevents a single early-arriving malicious message from permanently locking the slot.

3. **Track the TODO**: the comment at line 872 already identifies this as a pre-1.0 blocker. Ensure it is resolved before the network opens to untrusted peers.

---

### Proof of Concept

```
Preconditions:
  - Attacker is a connected p2p peer (no stake required).
  - Current height = H, round = 0.
  - Committee for H+1 is public; expected proposer for (H+1, round=0) = P.

Step 1: Attacker constructs ProposalInit {
    height: H+1,
    round: 0,
    proposer: P,   // correct field value, deterministically known
    ...
}
and sends it as a proposal stream whose first part is this init and whose
remaining parts are empty / immediately closed.

Step 2: handle_proposal receives the stream.
  - content_receiver.try_next() succeeds (init is the first part).
  - get_proposer_for_height(H+1, 0) == P == init.proposer  → check passes.
  - ord == Greater  → cache_future_proposal called.
  - or_insert stores (malicious_init, malicious_receiver) for (H+1, 0).

Step 3: Real proposer P sends the legitimate proposal for (H+1, 0).
  - handle_proposal receives it.
  - Proposer check passes.
  - cache_future_proposal called → or_insert is a no-op (slot already taken).
  - Legitimate stream is dropped.

Step 4: Height advances to H+1.
  - process_start_height retrieves cached proposals → gets (malicious_init, malicious_receiver).
  - handle_proposal_known_proposal_init inserts malicious stream into
    current_height_proposals_streams[(H+1, 0)].
  - shc.handle_proposal(malicious_init) → inserts round 0 into pending_validation_rounds
    → returns SMRequest::StartValidateProposal.
  - execute_requests removes stream, calls validate_proposal(malicious_init, malicious_receiver).
  - Validation fails / times out (empty content).

Step 5: Legitimate proposal arrives as a current-height message.
  - shc.handle_proposal checks pending_validation_rounds.contains(0) == true
    → returns VecDeque::new()  (silently dropped).

Step 6: Node votes nil for round 0. Attacker repeats for round 1, 2, …
  → Node never reaches a decision.
``` [6](#0-5) [7](#0-6) [5](#0-4)

### Citations

**File:** crates/apollo_consensus/src/manager.rs (L349-359)
```rust
    fn cache_future_proposal(
        &mut self,
        init: ProposalInit,
        content_receiver: mpsc::Receiver<ContextT::ProposalPart>,
    ) {
        self.future_proposals_cache
            .entry(init.height)
            .or_default()
            .entry(init.round)
            .or_insert((init, content_receiver));
    }
```

**File:** crates/apollo_consensus/src/manager.rs (L700-706)
```rust
        let cached_proposals = self.cache.get_current_height_proposals(height);
        trace!("Cached proposals for height {}: {:?}", height, cached_proposals);
        for (init, content_receiver) in cached_proposals {
            let new_requests =
                self.handle_proposal_known_proposal_init(height, shc, init, content_receiver).await;
            pending_requests.extend(new_requests);
        }
```

**File:** crates/apollo_consensus/src/manager.rs (L849-866)
```rust
                let Ok(proposer) =
                    get_proposer_for_height(&self.committee_provider, init.height, init.round)
                        .await
                else {
                    warn!(
                        "VIRTUAL_PROPOSER_LOOKUP_FAILED: Failed to determine virtual proposer for \
                         height {} round {}. Dropping proposal.",
                        init.height.0, init.round
                    );
                    return Ok(VecDeque::new());
                };
                if proposer != init.proposer {
                    warn!(
                        "Invalid proposer for height {} and round {}: expected {:?}, got {:?}",
                        init.height.0, init.round, proposer, init.proposer
                    );
                    return Ok(VecDeque::new());
                }
```

**File:** crates/apollo_consensus/src/manager.rs (L867-880)
```rust
                if ord == std::cmp::Ordering::Greater {
                    if self.cache.should_cache_proposal(&height, 0, &init) {
                        debug!("Received a proposal for a future height. {:?}", init);
                        // Note: new proposals with the same height/round will be ignored.
                        //
                        // TODO(matan): This only work for trusted peers. In the case of
                        // possibly malicious peers this is a
                        // possible DoS attack (malicious
                        // users can insert invalid/bad/malicious proposals before
                        // "good" nodes can propose).
                        //
                        // When moving to version 1.0 make sure this is addressed.
                        self.cache.cache_future_proposal(init, content_receiver);
                    }
```

**File:** crates/apollo_consensus/src/manager.rs (L909-919)
```rust
    async fn handle_proposal_known_proposal_init(
        &mut self,
        height: BlockNumber,
        shc: &mut SingleHeightConsensus,
        init: ProposalInit,
        content_receiver: mpsc::Receiver<ContextT::ProposalPart>,
    ) -> Requests {
        // Store the stream; requests will reference it by (height, round)
        self.current_height_proposals_streams.insert((height, init.round), content_receiver);
        shc.handle_proposal(init)
    }
```

**File:** crates/apollo_consensus/src/single_height_consensus.rs (L124-129)
```rust
        if self.state_machine.has_proposal_for_round(init.round)
            || self.pending_validation_rounds.contains(&init.round)
        {
            warn!("Round {} already handled a proposal, ignoring", init.round);
            return VecDeque::new();
        }
```
