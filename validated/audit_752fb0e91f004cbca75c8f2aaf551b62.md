### Title
Unverified Vote Signer Identity Allows Forged Consensus Votes — (`crates/apollo_consensus/src/state_machine.rs`, `crates/apollo_consensus/src/single_height_consensus.rs`)

### Summary

The consensus layer accepts `Vote` messages and counts their weight based solely on the self-reported `voter: ContractAddress` field. No cryptographic signature verification is performed against the claimed voter's public key. Any network peer can forge a vote claiming to be any committee member, accumulating arbitrary voting weight and potentially driving a false quorum to commit a wrong block.

### Finding Description

`Vote` messages carry a `signature: RawSignature` field that is structurally present but never verified on the receiving side.

**Vote creation (proposer side)** — the signature is explicitly left empty with a TODO:

```rust
// state_machine.rs make_self_vote()
let vote = Vote {
    ...
    voter: self.id,
    // TODO(Asmaa): sign the vote
    signature: RawSignature::default(),
};
``` [1](#0-0) 

**Vote processing (validator side)** — `handle_vote` in `SingleHeightConsensus` looks up the voter's weight from the committee by address alone, with no signature check:

```rust
fn vote_weight(&self, voter: ValidatorId) -> u128 {
    self.committee.members().iter()
        .find(|s| s.address == voter)
        .expect("voter must be in committee")
        .weight.0
}
``` [2](#0-1) 

The `verify_precommit_vote_signature` function exists in `signature_manager.rs` and is fully implemented:

```rust
pub fn verify_precommit_vote_signature(
    block_hash: BlockHash,
    signature: RawSignature,
    public_key: PublicKey,
) -> SignatureVerificationResult<bool> { ... }
``` [3](#0-2) 

…but it is never called in the vote-processing path. The `Vote` struct's `signature` field is received over the wire: [4](#0-3) [5](#0-4) 

…but is silently ignored. All test utilities confirm this by constructing votes with `signature: RawSignature::default()` and expecting them to be accepted: [6](#0-5) 

Contrast this with the **proposer** identity check, which IS enforced: `manager.rs` calls `get_proposer_for_height` and rejects any `ProposalInit` whose `init.proposer` does not match the committee-derived expected proposer: [7](#0-6) [8](#0-7) 

The proposer check is enforced; the voter check is not. This is the exact structural gap.

### Impact Explanation

An attacker who can send P2P messages to a validator node can craft `Vote` messages with `voter` set to any high-weight committee member. Because the consensus layer counts weight by address lookup without verifying the signature, the attacker can accumulate a 2/3 quorum of forged precommit votes for an arbitrary `proposal_commitment`. This causes `decision_reached` to be called with a commitment the honest node never validated, leading to:

- Wrong block committed to storage and state sync
- Wrong state root, receipts, events, and L1 messages anchored on-chain
- Incorrect fee accounting and balance changes

This maps to the **Critical** impact category: *Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input.*

### Likelihood Explanation

The attack surface is any peer that can reach the consensus P2P port. The `Vote` protobuf message is accepted from the network, deserialized, and processed without any cryptographic gate. The TODO comment confirms this is not a deliberate design choice but an unimplemented control. The `verify_precommit_vote_signature` utility already exists, so the fix is a wiring problem, not a design problem.

### Recommendation

Before counting any incoming `Vote`, verify its `signature` against the public key associated with `vote.voter` in the committee:

1. Extend the committee/staker data model to include each validator's public key (a `public_key` field already exists on `Staker` as `Felt::ZERO` in tests — populate it from the staking contract).
2. In `SingleHeightConsensus::handle_vote` (or in `manager.rs` before dispatching to SHC), call `verify_precommit_vote_signature(block_hash_or_vote_digest, vote.signature, committee_public_key)` and drop the vote if verification fails.
3. Remove the `// TODO(Asmaa): sign the vote` placeholder and implement actual signing in `make_self_vote`.

### Proof of Concept

```
1. Attacker connects to a validator's consensus P2P port.
2. Attacker observes the current height H and round R from streamed ProposalInit messages.
3. Attacker identifies the top-weight committee members V1, V2, V3 (addresses are public).
4. Attacker constructs three Vote messages:
     Vote { vote_type: Precommit, height: H, round: R,
            proposal_commitment: Some(<target_commitment>),
            voter: V1, signature: RawSignature::default() }
   (repeated for V2, V3 with sufficient combined weight ≥ 2/3 total)
5. Attacker sends these messages to the validator node.
6. manager.rs receives each Vote, dispatches to shc.handle_vote().
7. handle_vote() looks up V1/V2/V3 in the committee by address — they exist.
8. vote_weight() returns their real weights; quorum is reached.
9. StateMachine emits SMRequest::Decision for <target_commitment>.
10. context.decision_reached() is called; the wrong block is committed.
``` [9](#0-8) [10](#0-9)

### Citations

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

**File:** crates/apollo_consensus/src/state_machine.rs (L243-284)
```rust
    fn make_self_vote(
        &mut self,
        vote_type: VoteType,
        proposal_commitment: Option<ProposalCommitment>,
    ) -> VecDeque<SMRequest> {
        let vote = Vote {
            vote_type,
            height: self.height,
            round: self.round,
            proposal_commitment,
            voter: self.id,
            // TODO(Asmaa): sign the vote
            signature: RawSignature::default(),
        };
        let mut output = VecDeque::new();
        // Only non-observers record and track self-votes.
        if self.is_observer {
            return output;
        }
        let weight = self.vote_weight(self.id);
        let (votes_map, last_self_vote) = match vote_type {
            VoteType::Prevote => (&mut self.prevotes, &mut self.last_self_prevote),
            VoteType::Precommit => (&mut self.precommits, &mut self.last_self_precommit),
        };
        // Record the vote in the appropriate map.
        let inserted = votes_map.insert((self.round, self.id), (vote.clone(), weight)).is_none();
        assert!(
            inserted,
            "This should never happen: duplicate self {:?} vote for round={}, id={}",
            vote_type, self.round, self.id
        );
        // Update the latest self vote.
        assert!(
            last_self_vote.as_ref().is_none_or(|last| self.round > last.round),
            "State machine must progress in time: last_vote: {last_self_vote:?} new_vote: {vote:?}"
        );
        *last_self_vote = Some(vote.clone());
        // Returns VecDeque instead of a single SMRequest so callers can chain requests using
        // append().
        info!("Broadcasting {vote:?}");
        output.push_back(SMRequest::BroadcastVote(vote));
        output
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

**File:** crates/apollo_protobuf/src/proto/p2p/proto/consensus/consensus.proto (L20-36)
```text
message Vote {
    enum  VoteType {
        Prevote   = 0;
        Precommit = 1;
    };

    // We use a type field to distinguish between prevotes and precommits instead of different
    // messages, to make sure the data, and therefore the signatures, are unambiguous between
    // Prevote and Precommit.
    VoteType      vote_type           = 2;
    uint64        height              = 3;
    uint32        round               = 4;
    // This is optional since a vote can be NIL.
    optional Hash proposal_commitment = 5;
    Address       voter               = 6;
    Hashes        signature           = 7;
}
```

**File:** crates/apollo_consensus/src/test_utils.rs (L126-141)
```rust
pub fn prevote(
    block_felt: Option<Felt>,
    height: BlockNumber,
    round: Round,
    voter: ValidatorId,
) -> Vote {
    let proposal_commitment = block_felt.map(ProposalCommitment);
    Vote {
        vote_type: VoteType::Prevote,
        height,
        round,
        proposal_commitment,
        voter,
        signature: RawSignature::default(),
    }
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

**File:** crates/apollo_consensus/src/single_height_consensus.rs (L98-141)
```rust
    #[instrument(skip_all)]
    pub(crate) fn start(&mut self) -> Requests {
        self.state_machine.start()
    }

    /// Process the proposal init message and initiate block validation by returning
    /// `SMRequest::StartValidateProposal` to the manager.
    #[instrument(skip_all)]
    pub(crate) fn handle_proposal(&mut self, init: ProposalInit) -> Requests {
        debug!("Received {init:?}");
        let height = self.state_machine.height();
        if init.height != height {
            warn!("Invalid proposal height: expected {:?}, got {:?}", height, init.height);
            return VecDeque::new();
        }
        // TODO(guyn): replace this with assert_eq, but also need to fix simulation_test.
        let Ok(proposer_id) = self.committee.get_proposer(height, init.round) else {
            return VecDeque::new();
        };
        if init.proposer != proposer_id {
            warn!("Invalid proposer: expected {:?}, got {:?}", proposer_id, init.proposer);
            return VecDeque::new();
        }
        // Avoid duplicate validations:
        // - If SM already has an entry for this round, a (re)proposal was already recorded.
        // - If we already started validating this round, ignore repeats.
        if self.state_machine.has_proposal_for_round(init.round)
            || self.pending_validation_rounds.contains(&init.round)
        {
            warn!("Round {} already handled a proposal, ignoring", init.round);
            return VecDeque::new();
        }
        let timeout = self.timeouts.get_proposal_timeout(init.round);
        info!(
            "Accepting {init:?}. node_round: {}, timeout: {timeout:?}",
            self.state_machine.round()
        );
        CONSENSUS_PROPOSALS_ACCEPTED_FOR_VALIDATION.increment(1);

        // Since validating the proposal is non-blocking, avoid validating the same round twice in
        // parallel (e.g., due to repeats or spam).
        self.pending_validation_rounds.insert(init.round);
        // Ask the manager to start validation.
        VecDeque::from([SMRequest::StartValidateProposal(init)])
```
