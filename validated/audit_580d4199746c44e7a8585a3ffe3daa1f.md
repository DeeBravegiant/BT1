### Title
Consensus Votes Accepted Without Signature Verification Allows Any Peer to Forge Quorum by Impersonating Multiple Validators — (`crates/apollo_consensus/src/single_height_consensus.rs`)

---

### Summary

`handle_vote` in `SingleHeightConsensus` accepts peer votes without verifying the cryptographic signature on the `Vote` struct. The duplicate-vote guard tracks by `(round, voter_address)`, but since `voter` is a self-reported `ContractAddress` with no cryptographic binding, a single malicious peer can broadcast votes claiming to be every committee member in sequence, accumulating their combined stake weight and forging a Byzantine quorum unilaterally.

---

### Finding Description

The `Vote` struct carries a `signature: RawSignature` field defined in the protobuf schema and the Rust type: [1](#0-0) 

When a node creates its own vote, the signature slot is explicitly left empty with a `TODO`: [2](#0-1) 

When a peer vote arrives, `handle_vote` performs two checks — height match and committee membership — but the signature is never verified: [3](#0-2) 

The duplicate guard that follows tracks by the key `(round, vote.voter)`: [4](#0-3) 

Because `vote.voter` is a plain `ContractAddress` supplied by the sender with no cryptographic proof of ownership, a malicious peer can craft a sequence of votes — one per committee member — each with a distinct `voter` address. Every vote passes the committee-membership check and is treated as `VoteStatus::New` because no prior entry exists for that `(round, voter)` key. The weight accumulated for each accepted vote is the legitimate staking weight of the impersonated validator: [5](#0-4) 

The committee's `public_key` field per staker is available in the configuration, confirming that the keys needed for verification exist but are unused: [6](#0-5) 

The `SignatureManager` and `verify_precommit_vote_signature` helpers already exist in the codebase but are not wired into `handle_vote`: [7](#0-6) 

**Analog to the external report:** In `StoryBadgeNFT`, the deduplication guard (`usedSignatures`) was keyed on the *signature* rather than the *user address*, so a signer rotation let the same user bypass it. Here, the deduplication guard (`received_vote`) is keyed on the *claimed* voter address rather than a *verified* cryptographic identity, so the absence of signature verification lets the same peer bypass it by cycling through different claimed identities.

---

### Impact Explanation

A single peer connected to the consensus broadcast channel can:

1. Enumerate all committee members for the current height.
2. For each member, craft a `Vote` with `voter = member.address`, `signature = RawSignature::default()`, and any desired `proposal_commitment`.
3. Broadcast each vote; every one is accepted as `VoteStatus::New`.
4. Accumulate the full committee weight, satisfying the Byzantine 2/3 quorum threshold alone.
5. Drive `DecisionReached` for an arbitrary `ProposalCommitment`, committing a block that the honest majority never agreed to.

This produces wrong committed state — a Critical impact under the allowed scope ("Wrong state … from blockifier/syscall/execution logic for accepted input").

---

### Likelihood Explanation

Any peer that can reach the consensus broadcast channel (i.e., any node in the p2p network) can trigger this without any privileged access. The attack requires only the ability to send protobuf `Vote` messages, which is the normal operation of the consensus gossip layer. No special key material is needed because signatures are never checked.

---

### Recommendation

Implement signature verification inside `handle_vote` before the committee-membership check:

1. Retrieve the committee member's `public_key` for `vote.voter`.
2. Call `verify_precommit_vote_signature` (or an equivalent prevote variant) using the vote's content as the message digest and `vote.signature` as the signature.
3. Reject the vote if verification fails.

Additionally, `make_self_vote` must actually sign the vote using `SignatureManager::sign_precommit_vote` (or a prevote equivalent) so that honest nodes produce verifiable votes.

---

### Proof of Concept

```rust
// Attacker controls one peer connected to the consensus broadcast channel.
// Committee: [validator_A (weight 4), validator_B (weight 3), validator_C (weight 3)].
// Total weight = 10. Byzantine quorum requires > 6.67, i.e., weight >= 7.

// Step 1: forge a prevote for each committee member
for (member_address, _weight) in committee.members() {
    let forged_vote = Vote {
        vote_type: VoteType::Prevote,
        height: current_height,
        round: current_round,
        proposal_commitment: Some(attacker_chosen_commitment),
        voter: *member_address,          // claimed, not proven
        signature: RawSignature::default(), // empty — never checked
    };
    broadcast_channel.send(forged_vote).await;
}
// After three sends: accumulated weight = 4+3+3 = 10 > 6.67 → prevote quorum.

// Step 2: repeat for precommits → DecisionReached fires with attacker_chosen_commitment.
```

No existing guard in `handle_vote` or `received_vote` blocks this sequence because each `(round, voter)` key is distinct and no signature is ever verified. [8](#0-7)

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

**File:** crates/apollo_consensus/src/state_machine.rs (L194-203)
```rust
    fn vote_weight(&self, voter: ValidatorId) -> u128 {
        // TODO(Dafna): Use HashMap
        self.committee
            .members()
            .iter()
            .find(|s| s.address == voter)
            .expect("voter must be in committee")
            .weight
            .0
    }
```

**File:** crates/apollo_consensus/src/state_machine.rs (L207-241)
```rust
    pub(crate) fn received_vote(&self, vote: &Vote) -> VoteStatus {
        let determine_status = |old: &Vote, new: &Vote| {
            if old.proposal_commitment == new.proposal_commitment {
                VoteStatus::Duplicate
            } else {
                VoteStatus::Conflict(old.clone(), new.clone())
            }
        };

        // Check Map
        let key = (vote.round, vote.voter);
        let map_entry = match vote.vote_type {
            VoteType::Prevote => self.prevotes.get(&key),
            VoteType::Precommit => self.precommits.get(&key),
        };

        if let Some((old_vote, _)) = map_entry {
            return determine_status(old_vote, vote);
        }

        // Check Queue
        for event in &self.events_queue {
            let queued_vote = match (event, vote.vote_type) {
                (StateMachineEvent::Prevote(v), VoteType::Prevote) => v,
                (StateMachineEvent::Precommit(v), VoteType::Precommit) => v,
                _ => continue,
            };

            if queued_vote.round == vote.round && queued_vote.voter == vote.voter {
                return determine_status(queued_vote, vote);
            }
        }

        VoteStatus::New
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

**File:** crates/apollo_consensus/src/single_height_consensus.rs (L241-281)
```rust
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

**File:** crates/apollo_staking_config/src/config.rs (L19-24)
```rust
pub struct ConfiguredStaker {
    pub address: ContractAddress,
    pub weight: StakingWeight,
    pub public_key: Felt,
    pub can_propose: bool,
}
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
