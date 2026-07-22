### Title
Missing Vote Signature Verification Allows Forged Consensus Votes — (`crates/apollo_consensus/src/single_height_consensus.rs`)

### Summary

`handle_vote()` in `SingleHeightConsensus` checks that `vote.voter` is a member of the committee (address/identity check) but never verifies the cryptographic signature on the vote. Any unprivileged network peer can broadcast a `Vote` message with `voter` set to any legitimate validator's address and `signature` set to the default empty value, and the vote will be accepted and counted toward quorum as if it came from that validator.

### Finding Description

In `handle_vote()`, the only authorization check performed is a committee membership lookup by address:

```rust
// TODO(Asmaa): verify the signature   ← explicit acknowledgment of missing check
if !self.committee.members().iter().any(|s| s.address == vote.voter) {
    debug!("Ignoring vote from non validator: vote={:?}", vote);
    return VecDeque::new();
}
``` [1](#0-0) 

This is structurally identical to the external Solana bug: the code verifies that the claimed identity (`vote.voter`) matches a known address, but never verifies that the message was actually signed by the holder of the corresponding private key.

The `Vote` struct carries a `signature: RawSignature` field specifically for this purpose, and `verify_precommit_vote_signature` exists in `apollo_signature_manager`, but neither is called anywhere in the vote-handling path. The signature field is populated with `RawSignature::default()` (an all-zero value) even for legitimately self-produced votes:

```rust
// TODO(Asmaa): sign the vote
signature: RawSignature::default(),
``` [2](#0-1) 

The verification function exists but is only exercised in unit tests, never called from the consensus hot path: [3](#0-2) 

### Impact Explanation

An unprivileged attacker who can connect to the p2p network (a normal peer) can:

1. Enumerate the current validator committee (public information from the staking contract).
2. Craft `Vote` messages with `voter` set to any validator's `ContractAddress` and `signature` set to `RawSignature::default()`.
3. Broadcast these forged votes via the gossip channel.
4. Each receiving node's `handle_vote()` accepts the vote because the address is in the committee and no signature check is performed.
5. By forging votes from ≥ 2/3+1 distinct validator addresses, the attacker reaches quorum for an arbitrary `proposal_commitment`, causing the network to finalize a block the attacker chose.

The duplicate-vote guard (`VoteStatus::Duplicate`) only prevents the same `(round, voter)` pair from being counted twice; it does not prevent a forged vote from being the first (and only) vote recorded for a given validator.

This maps to: **Critical — wrong state/block commitment accepted through forged consensus votes**, and **Critical — invalid consensus decision reached without real validator authorization**.

### Likelihood Explanation

Any peer reachable on the p2p network can trigger this. No privileged key, no admin access, no leaked secret is required. The `Vote` struct is a plain protobuf message with no transport-layer authentication binding the sender to the `voter` field. The TODO comments confirm the check was intentionally deferred, not accidentally omitted, meaning the gap is present in the current production code path.

### Recommendation

Before accepting a vote into the state machine, verify the signature against the voter's registered public key:

```rust
pub(crate) fn handle_vote(&mut self, vote: Vote) -> Requests {
    // ... height check ...
    let Some(staker) = self.committee.members().iter().find(|s| s.address == vote.voter) else {
        debug!("Ignoring vote from non validator: vote={:?}", vote);
        return VecDeque::new();
    };
    // Verify the vote was actually signed by the claimed voter.
    let block_hash = BlockHash(vote.proposal_commitment.map_or(Felt::ZERO, |c| c.0));
    match verify_precommit_vote_signature(block_hash, vote.signature.clone(), staker.public_key) {
        Ok(true) => {}
        _ => {
            warn!("Invalid vote signature from {:?}, dropping", vote.voter);
            return VecDeque::new();
        }
    }
    // ... rest of handling ...
}
```

Correspondingly, `make_self_vote()` in `state_machine.rs` must actually sign the vote using `SignatureManager::sign_precommit_vote` before broadcasting it.

### Proof of Concept

```
Attacker (normal peer) connects to the gossip network.

Committee at height H = [ValidatorA (weight=100), ValidatorB (weight=100), ValidatorC (weight=100)]
Total weight = 300; quorum threshold = 201.

Attacker broadcasts three Vote messages:
  Vote { vote_type: Precommit, height: H, round: 0,
         proposal_commitment: Some(ATTACKER_CHOSEN_COMMITMENT),
         voter: ValidatorA.address,
         signature: RawSignature::default() }   // forged

  Vote { vote_type: Precommit, height: H, round: 0,
         proposal_commitment: Some(ATTACKER_CHOSEN_COMMITMENT),
         voter: ValidatorB.address,
         signature: RawSignature::default() }   // forged

  Vote { vote_type: Precommit, height: H, round: 0,
         proposal_commitment: Some(ATTACKER_CHOSEN_COMMITMENT),
         voter: ValidatorC.address,
         signature: RawSignature::default() }   // forged

Each honest node receives these votes. handle_vote() checks:
  ✓ vote.height == current height
  ✓ vote.voter ∈ committee.members()   (address match only)
  ✗ vote.signature is never verified

All three votes are accepted. Accumulated weight = 300 ≥ 201 (quorum).
StateMachineEvent::DecisionReached fires with ATTACKER_CHOSEN_COMMITMENT.
The network finalizes the attacker-chosen block.
``` [4](#0-3) [5](#0-4) [3](#0-2)

### Citations

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
