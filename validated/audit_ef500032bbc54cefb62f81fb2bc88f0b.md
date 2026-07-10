### Title
Pending Requests Permanently Stuck During Resharing Due to Stale Participant Set Validation in `respond*` Functions — (File: `crates/contract/src/state.rs`)

---

### Summary

The `respond`, `respond_ckd`, and `respond_verify_foreign_tx` contract functions validate the caller against the **current** active participant set via `assert_caller_is_attested_participant_and_protocol_active()`. During `Resharing` state, `active_participants()` returns the **new** (prospective) participant set, not the old one that was active when pending requests were submitted. A node being removed from the participant set may have computed a valid threshold signature and submitted a `respond` transaction just before or during the resharing transition. When that transaction lands in a block where the contract is already in `Resharing` state, the participant check panics because the removed node is not in the new set, causing the pending request to time out and become permanently unresolvable by that node.

---

### Finding Description

`respond`, `respond_ckd`, and `respond_verify_foreign_tx` all call `assert_caller_is_attested_participant_and_protocol_active()`: [1](#0-0) [2](#0-1) [3](#0-2) 

That helper fetches the participant set from `active_participants()`: [4](#0-3) 

`active_participants()` during `Resharing` returns the **new** prospective participants, not the old ones: [5](#0-4) 

The `respond` functions explicitly allow calls during resharing (`is_running_or_resharing()` check at line 575), but the participant gate uses only the new set. A node in the old set that is being removed is therefore blocked from submitting a valid response even though it was a legitimate member of the signing group when the request was created.

On the node side, the coordinator filters `running_participants` to the intersection of old and new sets during resharing: [6](#0-5) 

This prevents removed nodes from being assigned as leaders for **new** requests during resharing. However, it does not prevent a removed node from having a `respond` transaction already in flight — submitted in the window between when the contract transitioned to `Resharing` and when the node's indexer detected that state change. During that indexer-latency window, the node still believes it is in `Running` state and may submit a `respond` for a request it computed as leader.

When that transaction is included in a block where the contract is in `Resharing` state, the `assert_matches!` at line 2398–2402 panics with "Caller must be an attested participant", the transaction fails, and the pending yield is not resolved. After `REQUEST_EXPIRATION_BLOCKS` (200 blocks), the request times out via `fail_on_timeout`. [7](#0-6) 

---

### Impact Explanation

This is a **Medium** impact: request-lifecycle manipulation that breaks a production safety invariant. The invariant is: a cryptographically valid threshold signature response from a node that was a legitimate member of the signing group at request-submission time must be accepted by the contract. Instead, the contract silently rejects it based on the current (new) participant set, causing the pending request to time out. Users must resubmit, and time-sensitive cross-chain operations (e.g., bridge transactions with deadlines) may fail permanently if the timeout window expires before a new response can be computed and submitted by the intersection nodes.

---

### Likelihood Explanation

Every legitimate resharing that removes at least one participant creates this window. The window duration equals the indexer latency of the removed node (typically a few seconds to tens of seconds on NEAR). During that window, the removed node may still be the elected leader for one or more pending requests and may submit `respond` transactions that will be rejected. This is not a theoretical edge case — it is a natural consequence of the asynchronous gap between on-chain state transitions and off-chain node detection.

---

### Recommendation

During `Resharing`, `respond*` functions should accept callers who are members of **either** the previous running participant set or the new prospective participant set. Concretely, `active_participants()` for the `Resharing` arm should return the union (or at minimum the old set) for the purpose of `respond*` authorization:

```rust
// In active_participants() for Resharing:
ProtocolContractState::Resharing(state) => {
    // For respond* calls, also accept old participants.
    // One approach: return old participants here and add a separate
    // helper for governance calls that returns new participants.
    state.previous_running_state.parameters.participants()
}
```

Alternatively, introduce a separate `assert_caller_is_respond_eligible()` that checks membership in the union of old and new participant sets, and use it exclusively in `respond`, `respond_ckd`, and `respond_verify_foreign_tx`.

---

### Proof of Concept

1. Contract is in `Running` state with participants `{A, B, C}`, threshold 2.
2. User calls `sign(...)`. Node A is elected leader and computes the threshold signature with B.
3. Node A submits a `respond(request, signature)` transaction to NEAR.
4. Before the transaction is included, all three nodes vote `vote_new_parameters` to reshard to `{B, C, D}` (removing A). The contract transitions to `Resharing` state.
5. Node A's `respond` transaction is included in a block where the contract is in `Resharing` state.
6. `assert_caller_is_attested_participant_and_protocol_active()` calls `active_participants()`, which returns `{B, C, D}` (the new set).
7. Node A is not in `{B, C, D}`, so `is_caller_an_attested_participant` returns `Err(CallerNotParticipant)`.
8. The `assert_matches!` panics: "Caller must be an attested participant". The transaction fails.
9. The pending request is not resolved. After 200 blocks it times out via `fail_on_timeout`.
10. The user's sign request is permanently dropped; they must resubmit. [8](#0-7) [4](#0-3) [9](#0-8)

### Citations

**File:** crates/contract/src/lib.rs (L564-577)
```rust
    pub fn respond(
        &mut self,
        request: SignatureRequest,
        response: dtos::SignatureResponse,
    ) -> Result<(), Error> {
        let signer = Self::assert_caller_is_signer();

        log!("respond: signer={}, request={:?}", &signer, &request);

        self.assert_caller_is_attested_participant_and_protocol_active();

        if !self.protocol_state.is_running_or_resharing() {
            return Err(InvalidState::ProtocolStateNotRunning.into());
        }
```

**File:** crates/contract/src/lib.rs (L666-666)
```rust
        self.assert_caller_is_attested_participant_and_protocol_active();
```

**File:** crates/contract/src/lib.rs (L705-705)
```rust
        self.assert_caller_is_attested_participant_and_protocol_active();
```

**File:** crates/contract/src/lib.rs (L2389-2403)
```rust
    fn assert_caller_is_attested_participant_and_protocol_active(&self) {
        let participants = self.protocol_state.active_participants();

        Self::assert_caller_is_signer();

        let attestation_check = self
            .tee_state
            .is_caller_an_attested_participant(participants);

        assert_matches::assert_matches!(
            attestation_check,
            Ok(()),
            "Caller must be an attested participant"
        );
    }
```

**File:** crates/contract/src/state.rs (L255-270)
```rust
    pub fn active_participants(&self) -> &Participants {
        match self {
            ProtocolContractState::Initializing(state) => {
                state.generating_key.proposed_parameters().participants()
            }
            ProtocolContractState::Running(state) => state.parameters.participants(),
            ProtocolContractState::Resharing(state) => {
                state.resharing_key.proposed_parameters().participants()
            }
            ProtocolContractState::NotInitialized => {
                panic!(
                    "Protocol must be Initializing, Running, or Resharing to access active participants"
                );
            }
        }
    }
```

**File:** crates/node/src/coordinator.rs (L417-420)
```rust
        // Only consider the running participants that are also members of the new resharing state.
        running_participants
            .participants
            .retain(|p| participants_config.participants.contains(p));
```

**File:** crates/node/src/requests/queue.rs (L33-33)
```rust
pub const REQUEST_EXPIRATION_BLOCKS: NumBlocks = 200;
```
