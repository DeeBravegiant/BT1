### Title
Precommit Vote Message Digest Omits `chain_id`, `height`, and `round` — Signatures Replayable Across Rounds and Chains - (File: `crates/apollo_signature_manager/src/signature_manager.rs`)

---

### Summary

`build_precommit_vote_message_digest` constructs the ECDSA digest for consensus precommit votes using only a static domain separator (`PRECOMMIT_VOTE`) and the `block_hash`. It omits `chain_id`, `height`, `round`, and `voter`. The `Vote` wire struct carries all four of those fields, but none are covered by the signature. When vote-signature enforcement is activated (the code path is already wired; a single TODO guards the call site), a precommit signature produced for block hash `H` at height `N`, round `R`, chain `C` is cryptographically identical to one for the same hash at any other height, round, or chain. A malicious peer can strip a validator's genuine precommit from one context and re-attach it to a `Vote` message with different `height`/`round` fields; the signature will still pass `verify_precommit_vote_signature`.

---

### Finding Description

`build_precommit_vote_message_digest` in `crates/apollo_signature_manager/src/signature_manager.rs`:

```rust
fn build_precommit_vote_message_digest(block_hash: BlockHash) -> MessageDigest {
    let block_hash = block_hash.to_bytes_be();
    let mut message = Vec::with_capacity(PRECOMMIT_VOTE.len() + block_hash.len());
    message.extend_from_slice(PRECOMMIT_VOTE);   // static b"PRECOMMIT_VOTE"
    message.extend_from_slice(&block_hash);       // only the block hash
    MessageDigest(blake2s_to_felt(&message))
}
``` [1](#0-0) 

The `Vote` struct that carries this signature on the wire contains `height`, `round`, `proposal_commitment` (the block hash), `voter`, and `signature`: [2](#0-1) 

None of `height`, `round`, `voter`, or `chain_id` are hashed into the digest. The signing call site in `SignatureManager::sign_precommit_vote` passes only `block_hash`: [3](#0-2) 

The state machine currently creates votes with `signature: RawSignature::default()` and a TODO marks the enforcement gap: [4](#0-3) 

The `SignatureManager` component is fully wired and callable via `SignatureManagerRequest::SignPrecommitVote(BlockHash)`: [5](#0-4) 

`verify_precommit_vote_signature` is a public library function ready to be called: [6](#0-5) 

---

### Impact Explanation

Once the TODO is resolved and vote-signature verification is enforced, the broken digest allows:

1. **Cross-round replay at the same height.** A validator's genuine precommit for `(height=N, round=R, hash=H)` can be re-broadcast as `(height=N, round=R+k, hash=H)`. The height check in `handle_vote` filters cross-height replays, but no analogous guard exists for round. If the same block hash is proposed in a later round (e.g., a reproposal), the replayed signature verifies and the attacker contributes a fraudulent quorum vote.

2. **Cross-chain replay.** Starknet runs mainnet, Sepolia testnet, and integration networks. All share the same `SignatureManager` key infrastructure. A precommit signed on testnet for hash `H` is a valid signature for the same hash on mainnet. No `chain_id` is in the digest.

3. **Cross-voter impersonation is not possible** (the public key is checked separately), but the attacker can replay *the same validator's* vote in a different context, inflating that validator's apparent quorum contribution.

The corrupted value is the `Vote.signature` field: it is accepted as valid for a `(height, round, chain_id)` tuple the signing validator never intended to authorize.

Impact category: **High — Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.**

---

### Likelihood Explanation

The enforcement gap (TODO) is the only thing preventing exploitation today. The `SignatureManager` component, the `sign_precommit_vote` RPC, and `verify_precommit_vote_signature` are all production code. The moment a developer closes the TODO and wires verification into `handle_vote` or `SingleHeightConsensus`, the replay surface opens. Cross-round replay is the most immediately reachable vector because reproposals reuse the same block hash across rounds.

---

### Recommendation

Include `chain_id`, `height`, and `round` in the precommit vote message digest:

```rust
fn build_precommit_vote_message_digest(
    block_hash: BlockHash,
    chain_id: &ChainId,
    height: BlockNumber,
    round: Round,
) -> MessageDigest {
    // Poseidon or a length-prefixed concatenation of all fields
    MessageDigest(Poseidon::hash_array(&[
        Felt::try_from(chain_id).expect("valid chain id"),
        Felt::from(height.0),
        Felt::from(round),
        block_hash.0,
    ]))
}
```

Correspondingly update `SignatureManagerRequest::SignPrecommitVote` to carry `(BlockHash, ChainId, BlockNumber, Round)`, and pass those fields from the consensus manager when signing and verifying. This mirrors the fix applied to Opera-Bridge (adding destination chain ID and contract address to the message hash).

---

### Proof of Concept

1. Validator `V` signs a precommit for `(height=100, round=0, hash=H)` on Starknet mainnet. The digest is `blake2s("PRECOMMIT_VOTE" || H.to_bytes_be())`.

2. An observer records the `Vote { height=100, round=0, proposal_commitment=H, voter=V, signature=S }`.

3. In round 1 of height 100, the same block hash `H` is reproposed. The attacker broadcasts `Vote { height=100, round=1, proposal_commitment=H, voter=V, signature=S }`.

4. `verify_precommit_vote_signature(H, S, pubkey_of_V)` is called. It recomputes `blake2s("PRECOMMIT_VOTE" || H.to_bytes_be())` — identical to step 1 — and returns `true`.

5. The state machine records a valid precommit from `V` for round 1, even though `V` never signed for round 1. If this inflates the quorum count past the 2/3 threshold, `upon_decision` fires and a block is committed that did not receive a genuine supermajority. [7](#0-6)

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

**File:** crates/apollo_signature_manager_types/src/lib.rs (L92-95)
```rust
pub enum SignatureManagerRequest {
    SignIdentification(PeerId, Challenge),
    SignPrecommitVote(BlockHash),
}
```
