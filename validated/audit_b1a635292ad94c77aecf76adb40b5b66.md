### Title
Future-Proposal Cache "First-Wins" Race Allows Any Peer to Suppress the Legitimate Proposer's Block — (File: crates/apollo_consensus/src/manager.rs)

---

### Summary

`ConsensusCache::cache_future_proposal` stores the **first** proposal received for a given `(height, round)` pair and silently discards every subsequent one. Because the only sender check is on the self-reported `init.proposer` field — not on the libp2p `originated_peer_id` — any network peer can impersonate the expected proposer, inject a fake proposal for a future height before the real proposer sends theirs, and permanently evict the legitimate proposal from the cache. When that height becomes current, the validator processes the attacker's stream, which fails validation, and consensus times out. The code itself carries a `TODO` acknowledging this exact class of attack.

---

### Finding Description

**Root cause — `cache_future_proposal` uses `or_insert` (first-wins, no replacement):** [1](#0-0) 

```rust
fn cache_future_proposal(
    &mut self,
    init: ProposalInit,
    content_receiver: mpsc::Receiver<ContextT::ProposalPart>,
) {
    self.future_proposals_cache
        .entry(init.height)
        .or_default()
        .entry(init.round)
        .or_insert((init, content_receiver));   // ← first wins; real proposal silently dropped
}
```

**Proposer check is on the claimed field, not the transport sender:** [2](#0-1) 

The check `proposer != init.proposer` compares the expected committee proposer against the `init.proposer` field that the attacker controls in the protobuf message. The GossipSub layer delivers the raw `originated_peer_id` separately but it is never cross-checked against `init.proposer`: [3](#0-2) 

`ProposalInit.proposer` is a plain `ContractAddress` field in the wire format: [4](#0-3) 

**The code acknowledges the attack class:** [5](#0-4) 

```
// TODO(matan): This only work for trusted peers. In the case of
// possibly malicious peers this is a possible DoS attack (malicious
// users can insert invalid/bad/malicious proposals before
// "good" nodes can propose).
```

**Downstream effect — fake proposal is consumed instead of the real one:**

When the cached height becomes current, `get_current_height_proposals` returns the attacker's `(init, content_receiver)`: [6](#0-5) 

That receiver is passed directly to `validate_proposal`. If the attacker closed the stream immediately after sending `ProposalInit`, `handle_proposal_part` returns `HandledProposalPart::Failed("Proposal content stream was closed before receiving fin")`: [7](#0-6) 

The `fin_sender` is dropped, consensus sees no commitment, and the round times out. The real proposer's stream — which arrived after the cache slot was already filled — was silently discarded and is never retried.

---

### Impact Explanation

**Impact: High — valid proposals (and the transactions they carry) are rejected before sequencing.**

An attacker who can send a single GossipSub message per future `(height, round)` pair can prevent any block from being decided at that round. Repeating across rounds causes indefinite liveness failure: no transactions are sequenced, no blocks are produced. This maps to the allowed High impact: *"Mempool/gateway/RPC admission … rejects valid transactions before sequencing"* — here the consensus admission layer rejects the legitimate proposer's content before it ever reaches the batcher.

---

### Likelihood Explanation

**High.** The committee schedule is deterministic and public; any observer can compute the expected proposer for every future `(height, round)`. The GossipSub topic for proposals is open to all connected peers. The attacker needs only to:

1. Connect to the network (no stake, no special role required).
2. Compute `expected_proposer = get_proposer(height+1, round=0)`.
3. Craft a `ProposalInit` with `proposer = expected_proposer`, `height = H+1`, `round = 0`, and immediately close the content channel.
4. Broadcast it before the real proposer sends their proposal (trivially achievable because the real proposer only starts building at the moment height H is decided).

The attack is repeatable per round and requires no cryptographic material.

---

### Recommendation

1. **Bind transport identity to the proposer field.** When a proposal stream arrives, verify that the libp2p `originated_peer_id` corresponds to the Starknet operational key of `init.proposer` (the `StarkAuthentication` / `ChallengeAndIdentity` handshake already exists for this purpose): [8](#0-7) 

2. **Replace `or_insert` with authenticated replacement.** Once sender identity is verified, allow the real proposer to overwrite a previously cached fake entry for the same `(height, round)`.

3. **Short-term mitigation.** Restrict proposal stream acceptance to peers whose libp2p peer ID maps to a known committee member, rejecting streams from non-committee peers before they reach `cache_future_proposal`.

---

### Proof of Concept

```
Setup:
  - Network is at height H, round 0.
  - Committee for height H+1, round 0 has expected proposer P (public, deterministic).

Attack:
1. Attacker A connects to the GossipSub proposals topic (no authentication required).

2. A constructs a ProposalInit:
     height    = H+1
     round     = 0
     proposer  = P          // passes the proposer check
     fee_proposal_fri = arbitrary
     ... (other fields set to plausible values)

3. A opens a content channel, sends the ProposalInit as the first StreamMessage,
   then immediately closes the channel (no transactions, no ProposalFin).

4. handle_proposal() in manager.rs:
     - Reads first_part → ProposalInit (valid parse).
     - get_proposer_for_height(H+1, 0) == P == init.proposer → check passes.
     - ord == Greater → cache_future_proposal(init, closed_receiver) called.
     - future_proposals_cache[H+1][0] = (init, closed_receiver)  ← slot filled.

5. Real proposer P later sends its genuine proposal for (H+1, 0).
     - handle_proposal() is called again.
     - Proposer check passes.
     - cache_future_proposal called → or_insert finds existing entry → silently dropped.

6. Height H+1 becomes current. get_current_height_proposals(H+1) returns
   the attacker's (init, closed_receiver).

7. validate_proposal() is called with the closed receiver.
   handle_proposal_part() returns Failed("Proposal content stream was closed
   before receiving fin").
   fin_sender is dropped → consensus sees no commitment → round times out.

8. Attacker repeats for round 1, 2, … → indefinite liveness failure.
```

### Citations

**File:** crates/apollo_consensus/src/manager.rs (L267-286)
```rust
    fn get_current_height_proposals(
        &mut self,
        height: BlockNumber,
    ) -> Vec<(ProposalInit, mpsc::Receiver<ContextT::ProposalPart>)> {
        loop {
            let Some(entry) = self.future_proposals_cache.first_entry() else {
                return Vec::new();
            };
            match entry.key().cmp(&height) {
                std::cmp::Ordering::Greater => return Vec::new(),
                std::cmp::Ordering::Equal => {
                    let round_to_proposals = entry.remove();
                    return round_to_proposals.into_values().collect();
                }
                std::cmp::Ordering::Less => {
                    entry.remove();
                }
            }
        }
    }
```

**File:** crates/apollo_consensus/src/manager.rs (L348-359)
```rust
    /// Caches a proposal for a future height.
    fn cache_future_proposal(
        &mut self,
        init: ProposalInit,
        content_receiver: mpsc::Receiver<ContextT::ProposalPart>,
    ) {
        self.future_proposals_cache
            .entry(init.height)
            .or_default()
            .entry(init.round)
            .or_insert((init, content_receiver));
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

**File:** crates/apollo_consensus/src/manager.rs (L867-880)
```rust
                if ord == std::cmp::Ordering::Greater {
                    if self.cache.should_cache_proposal(&height, 0, &init) {
                        debug!("Received a proposal for a future height. {:?}", init);
                        // Note: new proposals with the same height/round will be ignored.
                        //
                        // TODO(matan): This only work for trusted peers. In the case of
                        // possibly malicious peers this is a
                        // possible DoS attack (malicious
                        // users can insert invalid/bad/malicious proposals before
                        // "good" nodes can propose).
                        //
                        // When moving to version 1.0 make sure this is addressed.
                        self.cache.cache_future_proposal(init, content_receiver);
                    }
```

**File:** crates/apollo_network/src/network_manager/mod.rs (L977-999)
```rust
    fn handle_gossipsub_behaviour_event(
        &mut self,
        event: gossipsub_impl::ExternalEvent,
    ) -> Result<(), NetworkError> {
        let gossipsub_impl::ExternalEvent::Received { originated_peer_id, message, topic_hash } =
            event;

        let message_size = message.len();
        self.update_broadcast_metric(&topic_hash, |broadcast_metrics| {
            broadcast_metrics.received_broadcast_message_metrics.record_message(message_size);
        });

        trace!("Received broadcast message with topic hash: {topic_hash:?}");
        let broadcasted_message_metadata = BroadcastedMessageMetadata {
            originator_id: OpaquePeerId::private_new(originated_peer_id),
            encoded_message_length: message_size,
        };
        let Some(sender) = self.broadcasted_messages_senders.get_mut(&topic_hash) else {
            panic!(
                "Received a message from a topic we're not subscribed to with hash {topic_hash:?}"
            );
        };
        let send_result = sender.try_send((message, broadcasted_message_metadata));
```

**File:** crates/apollo_protobuf/src/proto/p2p/proto/consensus/consensus.proto (L47-64)
```text
message ProposalInit {
    uint64 height                     = 1;
    uint32 round                      = 2;
    optional uint32 valid_round       = 3;
    Address proposer                  = 4;
    uint64 timestamp                  = 5;
    Address builder                   = 6;
    L1DataAvailabilityMode l1_da_mode = 7;
    Uint128 l2_gas_price_fri          = 8;
    Uint128 l1_gas_price_fri          = 9;
    Uint128 l1_data_gas_price_fri     = 10;
    Uint128 l1_gas_price_wei          = 11;
    Uint128 l1_data_gas_price_wei     = 12;
    string starknet_version           = 13;
    Hash version_constant_commitment   = 14;
    // Proposer's recommended fee for future blocks. Present iff Starknet version >= V0_14_3.
    optional Uint128 fee_proposal_fri = 15;
}
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L500-510)
```rust
    match proposal_part {
        None => {
            // Can happen due to:
            // 1. The StreamHandler evicted this stream.
            // 2. The stream was closed by the Proposer without sending ProposalFin.
            //    - Can occur if the Proposer can't complete the proposal (e.g. error during
            //      build_proposal).
            HandledProposalPart::Failed(
                "Proposal content stream was closed before receiving fin".to_string(),
            )
        }
```

**File:** crates/apollo_protobuf/src/authentication.rs (L1-28)
```rust
use serde::{Deserialize, Serialize};
use starknet_api::crypto::utils::{Challenge, PublicKey};
use starknet_types_core::felt::Felt;

// TODO(noam.s): Move this file/logic to the consensus manager crate once the whole stack is merged.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StarkAuthentication {
    pub message: StarkAuthenticationMessage,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum StarkAuthenticationMessage {
    ChallengeAndIdentity(ChallengeAndIdentity),
    Signature(Signature),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ChallengeAndIdentity {
    pub operational_public_key: PublicKey,
    pub challenge: Challenge,
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct Signature {
    pub signature: Vec<Felt>,
}


```
