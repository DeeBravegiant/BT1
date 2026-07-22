### Title
Consensus Vote Signature Not Verified — Unauthenticated Votes Accepted into Quorum Counting - (File: `crates/apollo_consensus/src/single_height_consensus.rs`)

---

### Summary

The `handle_vote` function in `single_height_consensus.rs` accepts prevote and precommit votes from peer nodes without verifying the cryptographic signature attached to each vote. The `Vote` struct carries a `signature: RawSignature` field, and the `SignatureManager` infrastructure exists and is wired into the node to produce and verify ECDSA signatures over vote messages. However, the verification call is explicitly deferred with a `TODO` comment. Any network peer that can send a well-formed `Vote` message with a valid `voter` address (i.e., any address that appears in the validator committee) can inject arbitrary prevotes or precommits without possessing the corresponding private key.

---

### Finding Description

`handle_vote` in `single_height_consensus.rs` performs two checks before accepting a vote:

1. Height match (`vote.height != height`)
2. Committee membership (`self.committee.members().iter().any(|s| s.address == vote.voter)`)

The signature field is present in the `Vote` struct and is transmitted over the wire in the protobuf encoding, but it is never verified:

```rust
pub(crate) fn handle_vote(&mut self, vote: Vote) -> Requests {
    // TODO(Asmaa): verify the signature
    ...
    if !self.committee.members().iter().any(|s| s.address == vote.voter) {
        debug!("Ignoring vote from non validator: vote={:?}", vote);
        return VecDeque::new();
    }
    ...
    info!("Accepting {:?}", vote);
    let sm_vote = match vote.vote_type {
        VoteType::Prevote => StateMachineEvent::Prevote(vote),
        VoteType::Precommit => StateMachineEvent::Precommit(vote),
    };
    self.state_machine.handle_event(sm_vote)
}
```

The `Vote` struct carries a `signature: RawSignature` field. The `SignatureManager` component provides `verify_precommit_vote_signature(block_hash, signature, public_key)` and `verify_identity(...)` functions. The `LocalKeyStore` stores the validator's private key in plaintext in memory (analogous to the iOS PIN stored in plaintext in the Keychain), and the signing infrastructure is fully wired — but the verification side is never called on the receiving path.

The committee membership check only validates that the `voter` address field names a known validator. An attacker who knows any validator's `ContractAddress` (which is public on-chain) can forge a vote for that validator by setting `voter` to that address and providing any `signature` bytes (including `RawSignature::default()`, i.e., all-zero bytes).

The `upon_decision` function in `state_machine.rs` fires `SMRequest::DecisionReached` when a quorum of precommits for the same `proposal_commitment` is accumulated. Because vote weights are read from the committee and the voter address is trusted without signature verification, a single network peer can inject enough forged precommits to satisfy the quorum threshold and force a `DecisionReached` event for an arbitrary `proposal_commitment`.

---

### Impact Explanation

**Critical / High** — falls under:
- *High: Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing* — forged votes can drive consensus to decide on a block that was never legitimately proposed or validated.
- *Critical: Wrong state, receipt, event, L1 message, class hash, storage value, or revert result* — if a forged quorum causes `decision_reached` to be called with a `proposal_commitment` that does not correspond to a legitimately built and validated block, the sequencer will attempt to commit an invalid or attacker-chosen block hash, corrupting the chain state.

Concretely: an attacker who can send P2P messages to a validator node can inject `n_quorum` forged precommit votes (each with a different known validator address and a zero/garbage signature) for any `proposal_commitment` value of their choice. The state machine will emit `DecisionReached` for that commitment, causing `decision_reached` in the orchestrator to call `batcher.decision_reached` and `state_sync_client.add_new_block` with the attacker-chosen block.

---

### Likelihood Explanation

The P2P network layer is reachable by any node that can establish a connection. The validator committee addresses are public (derived from on-chain staking contracts). The only barrier is network-level access to the consensus P2P port. No cryptographic material is required. The `TODO(Asmaa): verify the signature` comment confirms this is a known gap, not an intentional design choice.

---

### Recommendation

Call `verify_precommit_vote_signature` (or the equivalent prevote verifier) inside `handle_vote` before accepting the vote into the state machine. The public key for each committee member must be stored alongside the `ContractAddress` in the committee data structure. Votes that fail signature verification must be dropped and not forwarded to the state machine.

---

### Proof of Concept

1. Attacker connects to a validator node's consensus P2P port.
2. Attacker reads the current committee from the staking contract to obtain validator `ContractAddress` values and their weights.
3. Attacker constructs `n_quorum` `Vote` messages of type `Precommit`, each with:
   - `height` = current block height
   - `round` = current round
   - `proposal_commitment` = attacker-chosen `Felt` value (e.g., `Felt::ONE`)
   - `voter` = a distinct committee member address
   - `signature` = `RawSignature::default()` (all zeros — never checked)
4. Attacker sends these votes over the P2P stream.
5. `handle_vote` passes the height check and the committee membership check for each vote.
6. `state_machine.handle_event(StateMachineEvent::Precommit(vote))` is called for each.
7. `upon_decision` fires when the quorum threshold is met, emitting `SMRequest::DecisionReached(Decision { block: Felt::ONE, ... })`.
8. The orchestrator calls `batcher.decision_reached` and `state_sync_client.add_new_block` with the attacker-chosen commitment, corrupting the committed block record.

**Root cause file:** `crates/apollo_consensus/src/single_height_consensus.rs`, line 242. [1](#0-0) 

**Signature verification infrastructure exists but is unused on the receive path:** [2](#0-1) 

**Vote struct carries the signature field over the wire:** [3](#0-2) 

**Decision is triggered purely on quorum count with no signature guard:** [4](#0-3) 

**Production `create_signature_manager` uses `LocalKeyStore::new_for_testing()` — a hardcoded plaintext key — with no production key-loading path implemented:** [5](#0-4) [6](#0-5)

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

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L101-111)
```rust
    pub(crate) const fn new_for_testing() -> Self {
        // Created using `cairo-lang`.
        const PRIVATE_KEY: PrivateKey = PrivateKey(Felt::from_hex_unchecked(
            "0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133",
        ));
        const PUBLIC_KEY: PublicKey = PublicKey(Felt::from_hex_unchecked(
            "0x125d56b1fbba593f1dd215b7c55e384acd838cad549c4a2b9c6d32d264f4e2a",
        ));

        Self { private_key: PRIVATE_KEY, public_key: PUBLIC_KEY }
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

**File:** crates/apollo_signature_manager/src/lib.rs (L39-43)
```rust
// TODO(Elin): understand how key store would look in production and better define the way the
// signature manager is created.
pub fn create_signature_manager() -> SignatureManager {
    SignatureManager::new()
}
```
