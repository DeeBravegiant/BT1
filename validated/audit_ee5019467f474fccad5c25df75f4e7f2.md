### Title
Precommit Vote Signature Omits `height`, `round`, and `chain_id` — Signature Verification Unimplemented in Production Vote Handler (`crates/apollo_signature_manager/src/signature_manager.rs`, `crates/apollo_consensus/src/single_height_consensus.rs`)

---

### Summary

`build_precommit_vote_message_digest` signs only the `block_hash` (proposal commitment). The `Vote` wire struct carries `height`, `round`, `voter`, and `vote_type` as unsigned fields. Because those fields are absent from the signed payload, a valid precommit signature is replayable across rounds and chains. Compounding this, `handle_vote` in `SingleHeightConsensus` contains an explicit `// TODO(Asmaa): verify the signature` comment and performs **no signature check at all**, so any network peer can forge a vote from any validator address and have it accepted into the consensus state machine.

---

### Finding Description

**Signed payload construction** — `build_precommit_vote_message_digest`:

```rust
// crates/apollo_signature_manager/src/signature_manager.rs  lines 138-145
fn build_precommit_vote_message_digest(block_hash: BlockHash) -> MessageDigest {
    let block_hash = block_hash.to_bytes_be();
    let mut message = Vec::with_capacity(PRECOMMIT_VOTE.len() + block_hash.len());
    message.extend_from_slice(PRECOMMIT_VOTE);   // domain tag only
    message.extend_from_slice(&block_hash);       // proposal_commitment only
    MessageDigest(blake2s_to_felt(&message))
}
```

The signed bytes are `b"PRECOMMIT_VOTE" || block_hash`. The `Vote` struct fields `height`, `round`, `voter`, and `vote_type` are **not** committed to by the signature.

**Vote struct** (all fields unsigned except `signature`):

```rust
// crates/apollo_protobuf/src/consensus.rs  lines 53-61
pub struct Vote {
    pub vote_type: VoteType,
    pub height: BlockNumber,
    pub round: Round,
    pub proposal_commitment: Option<ProposalCommitment>,
    pub voter: ContractAddress,
    pub signature: RawSignature,   // covers only proposal_commitment
}
```

**Verification gap** — `handle_vote` in `SingleHeightConsensus`:

```rust
// crates/apollo_consensus/src/single_height_consensus.rs  lines 241-242
pub(crate) fn handle_vote(&mut self, vote: Vote) -> Requests {
    // TODO(Asmaa): verify the signature
```

The only checks performed are `vote.height == current_height` and `vote.voter ∈ committee`. No cryptographic verification is done.

**Reproposal reuse** — `update_for_reproposal` explicitly reuses the same `proposal_commitment` (block_hash) at a new round:

```rust
// crates/apollo_consensus/src/sequencer_consensus_context.rs  lines 218-228
let (mut init, transactions, proposal_id, finished_info) =
    self.get_proposal(height, &lookup_round, proposal_commitment).clone();
init.round = build_param.round;   // round changes, block_hash stays the same
```

Because `round` is not in the signed payload, a precommit signature produced at round 0 is cryptographically identical to one at round 1 for the same block.

---

### Impact Explanation

**Immediate (no verification):** Any network peer can craft a `Vote` message with `voter` set to any committee member's address and an arbitrary (or empty) `signature`. `handle_vote` accepts it unconditionally after the height and committee membership checks. With `f+1` forged precommit votes (Byzantine quorum threshold), a false consensus decision can be forced, committing an attacker-chosen block and producing wrong state, receipts, and events.

**Structural (missing fields):** Even after verification is wired in, the signed payload still omits `height`, `round`, and `chain_id`. Concretely:
- A validator's precommit signature from round 0 is valid as a round-1 precommit in any reproposal for the same block, because `round` is not bound.
- The same signature is valid on any chain that shares the same `block_hash` value, because `chain_id` is not bound.

Both paths satisfy the impact category: **Critical — wrong state/receipt/event from a false consensus decision**, and **High — signature/hash logic binds the wrong signer/context**.

---

### Likelihood Explanation

The verification gap is reachable by any peer that can send a `Vote` protobuf message over the broadcast channel. The consensus manager propagates all well-formed votes for the current height to `shc.handle_vote` without any prior authentication. No privilege is required. The reproposal cross-round replay requires observing a round-0 precommit and waiting for a reproposal, which is a normal Tendermint scenario.

---

### Recommendation

1. **Include `height`, `round`, `chain_id`, and `vote_type` in the signed payload** inside `build_precommit_vote_message_digest`:
   ```rust
   message.extend_from_slice(PRECOMMIT_VOTE);
   message.extend_from_slice(&chain_id.to_bytes());
   message.extend_from_slice(&height.0.to_be_bytes());
   message.extend_from_slice(&round.to_be_bytes());
   message.extend_from_slice(&block_hash.to_bytes_be());
   ```
2. **Implement signature verification in `handle_vote`** by calling `verify_precommit_vote_signature` (already present in `signature_manager.rs`) with the voter's public key from the committee, and reject votes that fail.
3. Ensure `sign_precommit_vote` is updated to pass all the new fields so signing and verification remain aligned.

---

### Proof of Concept

**Forged-vote path (no verification):**
1. Attacker connects as a p2p peer.
2. Attacker sends `f+1` `Vote` messages with `vote_type = Precommit`, `height = H`, `round = R`, `proposal_commitment = target_hash`, `voter = validatorX` (any committee member), `signature = RawSignature::default()`.
3. `handle_vote` passes the height check and committee-membership check, then calls `state_machine.handle_event(Precommit(vote))`.
4. With `f+1` such forged precommits, the state machine reaches precommit quorum and emits `DecisionReached`, committing the attacker-chosen block.

**Cross-round replay path (structural, after verification is added):**
1. Validator V signs a precommit for `block_hash = X` at `height = H`, `round = 0`. Signature `σ = sign("PRECOMMIT_VOTE" || X)`.
2. Consensus fails to reach decision at round 0; a reproposal reuses the same block at round 1 (same `proposal_commitment = X`).
3. Attacker replays V's round-0 vote with `round = 1` and the same `σ`. Verification passes because `round` is not in the signed payload.
4. V's vote is counted at round 1 without V's knowledge or consent, potentially contributing to a quorum V did not intend to form at that round. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** crates/apollo_consensus/src/single_height_consensus.rs (L241-242)
```rust
    pub(crate) fn handle_vote(&mut self, vote: Vote) -> Requests {
        // TODO(Asmaa): verify the signature
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
