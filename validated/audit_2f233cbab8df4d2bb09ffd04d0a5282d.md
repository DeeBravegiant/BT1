### Title
Consensus Vote Signature Never Verified — Any P2P Peer Can Forge Votes for Any Committee Member (`crates/apollo_consensus/src/single_height_consensus.rs`)

### Summary

`SingleHeightConsensus::handle_vote` accepts prevotes and precommits from the P2P network without verifying the cryptographic signature attached to each `Vote`. The only guard is a membership check on the self-reported `vote.voter` address, which is fully attacker-controlled. Separately, `build_precommit_vote_message_digest` constructs the signed payload from only `PRECOMMIT_VOTE || block_hash`, omitting `height`, `round`, and `vote_type`. This means that even once the TODO is resolved, a valid signature for one context is replayable in every other context that shares the same `block_hash`.

### Finding Description

**Root cause 1 — signature verification is entirely absent.**

`handle_vote` carries an explicit `// TODO(Asmaa): verify the signature` comment and never calls `verify_precommit_vote_signature`. The only check performed is:

```rust
if !self.committee.members().iter().any(|s| s.address == vote.voter) {
    debug!("Ignoring vote from non validator: vote={:?}", vote);
    return VecDeque::new();
}
```

`vote.voter` is a field in the protobuf message that any peer can set to any committee member's `ContractAddress`. The `vote.signature` field is deserialized and stored but never checked against the claimed voter's public key. The codebase itself acknowledges this in `manager.rs`:

> "Since vote signatures are not yet verified, a peer can forge votes with arbitrary voter addresses; without this cap that would grow `future_votes` without bound and exhaust memory."

The cap mentioned is a DoS bound only; it does not prevent vote forgery.

**Root cause 2 — signed payload omits height, round, and vote_type.**

`build_precommit_vote_message_digest` constructs:

```rust
fn build_precommit_vote_message_digest(block_hash: BlockHash) -> MessageDigest {
    let block_hash = block_hash.to_bytes_be();
    let mut message = Vec::with_capacity(PRECOMMIT_VOTE.len() + block_hash.len());
    message.extend_from_slice(PRECOMMIT_VOTE);
    message.extend_from_slice(&block_hash);
    MessageDigest(blake2s_to_felt(&message))
}
```

The digest is `blake2s("PRECOMMIT_VOTE" || block_hash)`. It does not bind:
- `height` — the same signature is valid at every block height that produces the same `ProposalCommitment`.
- `round` — the same signature is valid across all rounds of the same height.
- `vote_type` — a precommit signature is cryptographically identical to a prevote signature for the same `block_hash`, because neither the `VoteType` enum variant nor any domain separator distinguishes them.

This is the direct structural analog to the external report's missing nonce: the signed message lacks the context fields that would bind it to a single use.

### Impact Explanation

**Immediate (root cause 1):** Any node that can send messages on the P2P broadcast channel can craft a `Vote` with `voter` set to any committee member's address and `signature` set to `RawSignature::default()` (all-zero bytes). The receiving node's `handle_vote` will accept it as a genuine vote from that committee member, insert it into the prevote or precommit map, and trigger all downstream `upon_*` handlers. With a 4-validator Byzantine committee (quorum = 3), an attacker needs to inject three forged votes to reach quorum. If the attacker also controls the proposal stream (or the node has already validated a proposal), this can drive the node to a `DecisionReached` event for a block the honest quorum never agreed on.

**Future (root cause 2):** Once signature verification is wired in, a validator's legitimate precommit signature for `ProposalCommitment X` at height H, round R is also a valid precommit signature for `ProposalCommitment X` at height H+1, round 0 (cross-height replay), and is also a valid *prevote* signature for `ProposalCommitment X` at any height/round (cross-type replay). The cross-type replay is particularly dangerous: an attacker who observed a validator's precommit on the wire can replay it as a prevote in a later round, artificially inflating the prevote count and potentially triggering a premature prevote quorum.

### Likelihood Explanation

Any node that can connect to the P2P gossip layer and broadcast a `Vote` message can trigger root cause 1 today. The code comment in `manager.rs` explicitly acknowledges this as a known gap. Root cause 2 is latent and will become exploitable the moment the `// TODO(Asmaa): verify the signature` line is replaced with a real call to `verify_precommit_vote_signature` — at which point the incomplete digest will silently accept cross-height and cross-type replays.

### Recommendation

1. **Immediate:** In `handle_vote`, after the committee membership check, call `verify_precommit_vote_signature` (or the equivalent prevote verifier) using the committee member's registered public key. Reject the vote if the signature is invalid or if the signature field is the zero default.

2. **Digest fix:** Extend `build_precommit_vote_message_digest` (and a parallel `build_prevote_vote_message_digest`) to include `height`, `round`, `vote_type`, and optionally `chain_id` in the hashed payload, so that a signature is cryptographically bound to exactly one (height, round, type, block_hash) tuple and cannot be replayed across contexts.

### Proof of Concept

```
Attacker is any node connected to the P2P broadcast channel.
Committee: [V0 (proposer), V1, V2, V3], Byzantine quorum = 3.
Node under attack has validated proposal P with commitment C at height H, round 0.

Step 1: Attacker crafts three Vote messages:
  Vote { vote_type: Precommit, height: H, round: 0,
         proposal_commitment: Some(C),
         voter: V1,                        // legitimate committee address
         signature: RawSignature::default() }  // all-zero, never checked
  (repeat for V2, V3)

Step 2: Attacker broadcasts all three via the P2P gossip channel.

Step 3: handle_vote() on the victim node:
  - Checks vote.height == H  ✓
  - Checks vote.voter ∈ committee  ✓  (V1/V2/V3 are members)
  - Calls state_machine.received_vote() → VoteStatus::New  ✓
  - Inserts into precommits map with full weight
  - TODO(Asmaa): verify the signature  ← skipped

Step 4: upon_decision() fires:
  - precommits map now has C from V1, V2, V3 (forged) + possibly self
  - value_has_enough_votes() returns true
  - virtual_proposer_in_favor() returns true (attacker forged V0 if needed)
  - DecisionReached(C) emitted

Result: Node commits block C driven entirely by forged votes.
        Honest validators V1/V2/V3 never actually voted for C.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** crates/apollo_consensus/src/single_height_consensus.rs (L241-252)
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
```

**File:** crates/apollo_consensus/src/state_machine.rs (L243-256)
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
```

**File:** crates/apollo_consensus/src/state_machine.rs (L694-716)
```rust
    fn upon_decision(&mut self, round: u32) -> VecDeque<SMRequest> {
        let Some((Some(proposal_id), _)) = self.proposals.get(&round) else {
            return VecDeque::new();
        };
        if !self.value_has_enough_votes(&self.precommits, round, &Some(*proposal_id), &self.quorum)
        {
            return VecDeque::new();
        }
        if !self.virtual_proposer_in_favor(&self.precommits, round, &Some(*proposal_id)) {
            return VecDeque::new();
        }
        // Collect all supporting precommits for this proposal and round.
        let supporting_precommits: Vec<Vote> = self
            .precommits
            .iter()
            .filter(|(&(r, _voter), (v, _w))| {
                r == round && v.proposal_commitment == Some(*proposal_id)
            })
            .map(|(_vote_key, (v, _w))| v.clone())
            .collect();

        let decision = Decision { precommits: supporting_precommits, block: *proposal_id, round };
        VecDeque::from([SMRequest::DecisionReached(decision)])
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

**File:** crates/apollo_consensus/src/manager.rs (L327-330)
```rust
            // Bound the cache to what an honest committee could produce. Since vote signatures are
            // not yet verified, a peer can forge votes with arbitrary voter addresses; without this
            // cap that would grow `future_votes` without bound and exhaust memory.
            if votes.len() < cap {
```
