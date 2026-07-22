After thorough investigation of the codebase, I found a valid analog.

---

### Title
Vote Signatures Are Never Verified in Consensus — Any Peer Can Forge Validator Votes - (File: `crates/apollo_consensus/src/single_height_consensus.rs`)

### Summary

`SingleHeightConsensus::handle_vote` accepts prevotes and precommits from any network peer without verifying the ECDSA signature on the vote. The only admission check is that the claimed `voter` address appears in the committee member list. Because committee addresses are public, any peer can forge votes for any validator, manufacture false quorums, and drive the node to decide on an attacker-chosen block.

### Finding Description

**Unsigned votes are created and accepted without verification.**

In `state_machine.rs`, every self-vote is created with a zero signature and a TODO to sign it:

```rust
// TODO(Asmaa): sign the vote
signature: RawSignature::default(),
``` [1](#0-0) 

In `single_height_consensus.rs`, incoming votes are processed with an explicit TODO to verify the signature that is never fulfilled:

```rust
pub(crate) fn handle_vote(&mut self, vote: Vote) -> Requests {
    // TODO(Asmaa): verify the signature
    ...
    if !self.committee.members().iter().any(|s| s.address == vote.voter) {
        debug!("Ignoring vote from non validator: vote={:?}", vote);
        return VecDeque::new();
    }
``` [2](#0-1) 

The only guard is a membership check on the `voter` address field — a field the attacker supplies freely in the wire message. The `signature` field on `Vote` is never read in this path:

```rust
pub struct Vote {
    pub vote_type: VoteType,
    pub height: BlockNumber,
    pub round: Round,
    pub proposal_commitment: Option<ProposalCommitment>,
    pub voter: ContractAddress,
    pub signature: RawSignature,   // populated but never verified
}
``` [3](#0-2) 

The verification primitive **exists** but is never called in the vote-handling path:

```rust
pub fn verify_precommit_vote_signature(
    block_hash: BlockHash,
    signature: RawSignature,
    public_key: PublicKey,
) -> SignatureVerificationResult<bool> { ... }
``` [4](#0-3) 

### Impact Explanation

A single malicious peer that knows the committee member addresses (public information) can:

1. Craft `Vote` messages with `voter` set to any committee member address and `signature` set to any bytes (including `RawSignature::default()`).
2. Send enough forged prevotes to trigger a prevote quorum for an attacker-chosen `proposal_commitment`.
3. Send enough forged precommits to trigger a precommit quorum and a `DecisionReached` event for that commitment.

The result is that the node commits a block it never actually validated, producing a wrong state root, wrong receipts, and wrong event commitments — a critical consensus integrity failure.

### Likelihood Explanation

The trigger requires only a network-connected peer. Committee member addresses are derived from public staking data. No privileged access is needed. The attack is reachable from any external peer on the consensus gossip channel.

### Recommendation

Call `verify_precommit_vote_signature` (or an equivalent prevote verifier) inside `handle_vote` before the vote is forwarded to the state machine. The committee's `Staker::public_key` field already carries the per-validator public key needed for verification:

```rust
pub struct Staker {
    pub address: ContractAddress,
    pub weight: StakingWeight,
    pub public_key: Felt,   // use this for ECDSA verification
}
``` [5](#0-4) 

Reject any vote whose signature does not verify against the `public_key` of the matching committee member.

### Proof of Concept

```
1. Attacker joins the consensus gossip network as a peer.
2. Attacker reads the committee for height H (public staking data).
   Committee = [V1, V2, V3, V4]  (4 validators, quorum = 3)
3. Attacker sends three forged Prevote messages:
     Vote { vote_type: Prevote, height: H, round: 0,
            proposal_commitment: Some(ATTACKER_BLOCK),
            voter: V1, signature: RawSignature::default() }
     Vote { ..., voter: V2, ... }
     Vote { ..., voter: V3, ... }
4. handle_vote checks: height == H ✓, voter ∈ committee ✓, not duplicate ✓
   → all three votes are accepted, prevote quorum reached for ATTACKER_BLOCK.
5. Attacker repeats with Precommit votes for V1, V2, V3.
6. Precommit quorum reached → DecisionReached(ATTACKER_BLOCK).
7. Node commits a block it never executed, producing wrong state.
```

### Citations

**File:** crates/apollo_consensus/src/state_machine.rs (L254-256)
```rust
            // TODO(Asmaa): sign the vote
            signature: RawSignature::default(),
        };
```

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

**File:** crates/apollo_consensus/src/test_utils.rs (L208-215)
```rust
    let stakers = validators_with_weights
        .into_iter()
        .map(|(address, weight)| Staker {
            address,
            weight: StakingWeight(weight),
            public_key: Felt::ZERO,
        })
        .collect();
```
