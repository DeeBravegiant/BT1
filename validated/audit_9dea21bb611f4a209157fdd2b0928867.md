### Title
Precommit Vote Signature Omits `chain_id`, `height`, and `round` — Replay Across Chains and Heights Possible, and Signatures Are Not Yet Verified — (`crates/apollo_signature_manager/src/signature_manager.rs`)

---

### Summary

`build_precommit_vote_message_digest` signs only `PRECOMMIT_VOTE || block_hash`. It omits `chain_id`, `height`, `round`, and `vote_type`. A valid precommit signature produced by a legitimate validator for block hash `X` at height `H` on chain `C` is therefore replayable at any other height or round where the same `block_hash` value appears, and across any other chain where the same validator key is active. Compounding this, `handle_vote` in `single_height_consensus.rs` contains an explicit `// TODO(Asmaa): verify the signature` comment and performs **no cryptographic verification at all**, meaning any P2P peer can today inject forged precommit votes attributed to any committee member without possessing their private key.

---

### Finding Description

**Signature construction — missing binding context**

`build_precommit_vote_message_digest` in `crates/apollo_signature_manager/src/signature_manager.rs` constructs the signed payload as:

```
PRECOMMIT_VOTE || block_hash.to_bytes_be()
``` [1](#0-0) 

The fields `chain_id`, `height` (`BlockNumber`), `round`, and `vote_type` are absent from the digest. The `Vote` struct carries all four of these fields alongside the `signature`: [2](#0-1) 

Because the signed message does not commit to any of them, a signature produced for `(block_hash=X, height=H, round=R, chain=C)` is mathematically valid for `(block_hash=X, height=H', round=R', chain=C')` for any `H'`, `R'`, `C'`.

**Signature verification is entirely absent**

`handle_vote` in `single_height_consensus.rs` explicitly defers verification with a TODO comment and proceeds to accept the vote after only checking that `vote.voter` is a committee member: [3](#0-2) 

The `voter` field is attacker-controlled (it is deserialized from the wire message). No cryptographic check ties the `signature` field to the claimed `voter`. The `sign_precommit_vote` / `verify_precommit_vote_signature` API exists in `SignatureManager` but is never called on the receive path. [4](#0-3) [5](#0-4) 

---

### Impact Explanation

Any node connected to the P2P broadcast channel can craft a `Vote` message with `voter` set to any committee member's address and `vote_type = Precommit`, `proposal_commitment = <target_block_hash>`. Because `handle_vote` never calls `verify_precommit_vote_signature`, the forged vote passes all checks and is fed directly into the state machine as a genuine precommit. A single attacker can inject enough forged precommits to satisfy the Byzantine quorum threshold, causing the consensus engine to emit a `Decision` for a block that was never actually approved by the real validator set. The committed block, its state diff, receipts, events, and L1 messages are then written to storage and propagated as canonical — producing wrong state, wrong block hash, and wrong execution results.

Even after the TODO is resolved and verification is enabled, the missing `chain_id`/`height`/`round` binding means a legitimately captured precommit signature for block hash `X` at height `H` on one chain can be replayed at any height `H'` on any other chain where the same `block_hash` value coincidentally appears (e.g., genesis or a repeated empty-block hash), bypassing the cryptographic check entirely.

---

### Likelihood Explanation

The missing verification is reachable by any peer that can connect to the consensus broadcast topic — no privileged access is required. The committee member addresses are public (they appear in every `Vote` message on the wire). Constructing a forged `Vote` protobuf requires only knowledge of the target `proposal_commitment` value, which is broadcast in the proposal stream. The attack is therefore executable by any observer of the P2P network.

---

### Recommendation

1. **Immediately enforce signature verification in `handle_vote`**: call `verify_precommit_vote_signature` (or an equivalent) before accepting any vote into the state machine. Votes with invalid or missing signatures must be dropped and the peer reported.

2. **Extend `build_precommit_vote_message_digest` to bind all context fields**:

```rust
fn build_precommit_vote_message_digest(
    block_hash: BlockHash,
    chain_id: &ChainId,
    height: BlockNumber,
    round: Round,
    vote_type: VoteType,
) -> MessageDigest { ... }
```

Include `chain_id`, `height`, `round`, and `vote_type` in the hashed payload so that a signature is bound to exactly one vote at one position in one chain's consensus history.

3. Consider adopting a structured signing standard (analogous to EIP-712) that makes domain separation explicit and machine-verifiable.

---

### Proof of Concept

A malicious P2P peer constructs the following `Vote` protobuf targeting a known `proposal_commitment`:

```rust
// Attacker knows committee member addresses from any prior Vote broadcast.
let forged_vote = Vote {
    vote_type: VoteType::Precommit,
    height: current_height,          // from ProposalInit broadcast
    round: current_round,
    proposal_commitment: Some(target_commitment), // from ProposalFin broadcast
    voter: known_committee_member,   // any address in the committee
    signature: RawSignature::default(), // empty — never checked
};
```

`handle_vote` in `single_height_consensus.rs` checks only:

```rust
if vote.height != height { return VecDeque::new(); }
if !self.committee.members().iter().any(|s| s.address == vote.voter) {
    return VecDeque::new();
}
// TODO(Asmaa): verify the signature  ← never executed
``` [6](#0-5) 

The attacker sends `2f+1` such forged votes (one per distinct committee member address) to reach Byzantine quorum. The state machine emits `Decision(target_commitment)`, and `decision_reached` commits the corresponding block to storage as canonical. [7](#0-6)

### Citations

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L68-74)
```rust
    pub async fn sign_precommit_vote(
        &self,
        block_hash: BlockHash,
    ) -> SignatureManagerResult<RawSignature> {
        let message_digest = build_precommit_vote_message_digest(block_hash);
        self.sign(message_digest).await
    }
```

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L138-145)
```rust
fn build_precommit_vote_message_digest(block_hash: BlockHash) -> MessageDigest {
    let block_hash = block_hash.to_bytes_be();
    let mut message = Vec::with_capacity(PRECOMMIT_VOTE.len() + block_hash.len());
    message.extend_from_slice(PRECOMMIT_VOTE);
    message.extend_from_slice(&block_hash);

    MessageDigest(blake2s_to_felt(&message))
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

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L989-1024)
```rust
    async fn decision_reached(
        &mut self,
        height: BlockNumber,
        round: Round,
        commitment: ProposalCommitment,
        wait_for_last_commitment: bool,
    ) -> Result<(), ConsensusError> {
        info!("Finished consensus for height: {height}. Agreed on block: {:#066x}", commitment.0);

        self.interrupt_active_proposal().await;
        let (init, transactions, proposal_id, finished_info) = {
            let mut proposals = self.valid_proposals.lock().unwrap();
            let (init, transactions, proposal_id, finished_info) =
                proposals.get_proposal(&height, &round, &commitment).clone();
            proposals.remove_proposals_below_or_at_height(&height);
            (init, transactions, proposal_id, finished_info)
        };

        let decision_reached_response =
            self.deps.batcher.decision_reached(DecisionReachedInput { proposal_id }).await?;

        // CRITICAL: The block is now committed. This function must not fail beyond this point
        // unless the state is fully reverted, otherwise the node will be left in an
        // inconsistent state.

        self.finalize_decision(
            height,
            &init,
            commitment,
            transactions,
            decision_reached_response,
            finished_info.block_header_commitments.clone(),
            finished_info.l2_gas_used,
            wait_for_last_commitment,
        )
        .await;
```
