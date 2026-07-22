### Title
Unsigned Consensus Votes Accepted with Arbitrary `voter` Field — (`crates/apollo_consensus/src/single_height_consensus.rs`)

### Summary

`SingleHeightConsensus::handle_vote` accepts any incoming `Vote` message and counts it toward quorum using only the `voter: ContractAddress` field embedded in the message itself. The cryptographic signature carried in `Vote.signature` is never verified against the claimed `voter` address. Any network peer can therefore broadcast forged prevotes and precommits attributed to every committee member, manufacture a false quorum, and cause the consensus engine to commit an attacker-chosen `ProposalCommitment`.

### Finding Description

The `Vote` struct carries both a `voter` address and a `signature` field: [1](#0-0) 

`handle_vote` in `SingleHeightConsensus` checks only that `vote.voter` is a member of the committee. The explicit `// TODO(Asmaa): verify the signature` comment acknowledges that the cryptographic check is absent: [2](#0-1) 

Votes are created locally with `signature: RawSignature::default()` (all-zero bytes), confirming that no signing infrastructure is wired up yet: [3](#0-2) 

The `SignatureManager` crate already provides `verify_precommit_vote_signature` and the ECDSA primitives needed to close this gap, but they are never called on the inbound vote path: [4](#0-3) 

The manager's own comment in `cache_future_vote` explicitly acknowledges that voter addresses in future-height votes can be forged because signatures are not yet verified: [5](#0-4) 

### Impact Explanation

An attacker who is any reachable network peer can:

1. Enumerate the current committee (public on-chain data).
2. For each committee member `V_i`, broadcast a `Vote { voter: V_i, proposal_commitment: X, signature: RawSignature::default() }` where `X` is any commitment the attacker chooses.
3. `handle_vote` passes the committee-membership check for every `V_i` and forwards each vote to the state machine.
4. Once the state machine accumulates votes whose combined weight meets the Byzantine quorum threshold (≥ 2/3 total weight), it emits `SMRequest::DecisionReached` for the attacker-chosen commitment `X`.

The committed `ProposalCommitment` drives the block that is finalized, stored, and anchored to L1. A forged quorum therefore produces a wrong block hash, wrong state root, wrong receipts, and wrong events — matching the "wrong state, receipt, event … storage value" critical impact class.

### Likelihood Explanation

The attack requires only the ability to send broadcast messages on the consensus P2P topic, which is available to any peer that can connect to the network. No privileged access, no stake, and no knowledge of any private key is required. The only prerequisite is knowing the committee addresses, which are derived from public staking contract state. The attack is therefore reachable by any unprivileged network participant.

### Recommendation

**Short term:** In `SingleHeightConsensus::handle_vote`, after the committee-membership check, verify `vote.signature` against the voter's registered public key using `verify_precommit_vote_signature` (or an equivalent vote-specific digest). Reject any vote whose signature does not verify. The `SignatureManager` crate already contains the necessary primitives.

**Long term:** Remove the `// TODO(Asmaa): verify the signature` placeholder and add an invariant test asserting that no vote with an invalid or default signature can ever advance the state machine. Extend the existing `future_votes_capped` test to also assert that forged-voter votes (with invalid signatures) are rejected before being counted.

### Proof of Concept

```rust
// Attacker is any peer on the consensus broadcast channel.
// committee_members: Vec<ContractAddress> — obtained from public staking contract.
// chosen_commitment: ProposalCommitment — any value the attacker wants committed.

for member in committee_members {
    let forged_vote = Vote {
        vote_type: VoteType::Precommit,
        height: current_height,
        round: current_round,
        proposal_commitment: Some(chosen_commitment),
        voter: member,                    // claimed identity — never verified
        signature: RawSignature::default(), // all-zero bytes — accepted as-is
    };
    broadcast_channel.send(forged_vote).await;
}
// handle_vote passes the committee-membership check for every forged vote.
// State machine accumulates weight; quorum is reached; DecisionReached(chosen_commitment) fires.
```

The `handle_vote` path that accepts each forged vote without signature verification: [6](#0-5)

### Citations

**File:** crates/apollo_protobuf/src/consensus.rs (L53-61)
```rust
#[derive(Debug, Default, Hash, Clone, Eq, PartialEq, Serialize, Deserialize)]
pub struct Vote {
    pub vote_type: VoteType,
    pub height: BlockNumber,
    pub round: Round,
    pub proposal_commitment: Option<ProposalCommitment>,
    pub voter: ContractAddress,
    pub signature: RawSignature,
}
```

**File:** crates/apollo_consensus/src/single_height_consensus.rs (L239-281)
```rust
    /// Handle vote messages from peer nodes.
    #[instrument(skip_all)]
    pub(crate) fn handle_vote(&mut self, vote: Vote) -> Requests {
        // TODO(Asmaa): verify the signature
        trace!("Received {:?}", vote);
        let height = self.state_machine.height();
        if vote.height != height {
            warn!("Invalid vote height: expected {:?}, got {:?}", height, vote.height);
            return VecDeque::new();
        }
        if !self.committee.members().iter().any(|s| s.address == vote.voter) {
            debug!("Ignoring vote from non validator: vote={:?}", vote);
            return VecDeque::new();
        }

        // Check if vote has already been received.
        match self.state_machine.received_vote(&vote) {
            VoteStatus::Duplicate => {
                // Duplicate - ignore.
                trace_every_n_ms!(
                    DUPLICATE_VOTE_LOG_PERIOD_MS,
                    "Ignoring duplicate vote: {vote:?}"
                );
                return VecDeque::new();
            }
            VoteStatus::Conflict(old_vote, new_vote) => {
                // Conflict - ignore and record.
                warn!("Conflicting votes: old={old_vote:?}, new={new_vote:?}");
                CONSENSUS_CONFLICTING_VOTES.increment(1);
                return VecDeque::new();
            }
            VoteStatus::New => {
                // Vote is new, proceed to process it.
            }
        }

        info!("Accepting {:?}", vote);
        let sm_vote = match vote.vote_type {
            VoteType::Prevote => StateMachineEvent::Prevote(vote),
            VoteType::Precommit => StateMachineEvent::Precommit(vote),
        };
        self.state_machine.handle_event(sm_vote)
    }
```

**File:** crates/apollo_consensus/src/state_machine.rs (L248-256)
```rust
        let vote = Vote {
            vote_type,
            height: self.height,
            round: self.round,
            proposal_commitment,
            voter: self.id,
            // TODO(Asmaa): sign the vote
            signature: RawSignature::default(),
        };
```

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L179-186)
```rust
pub fn verify_precommit_vote_signature(
    block_hash: BlockHash,
    signature: RawSignature,
    public_key: PublicKey,
) -> SignatureVerificationResult<bool> {
    let message_digest = build_precommit_vote_message_digest(block_hash);
    verify_signature(message_digest, signature, public_key)
}
```

**File:** crates/apollo_consensus/src/manager.rs (L327-330)
```rust
            // Bound the cache to what an honest committee could produce. Since vote signatures are
            // not yet verified, a peer can forge votes with arbitrary voter addresses; without this
            // cap that would grow `future_votes` without bound and exhaust memory.
            if votes.len() < cap {
```
