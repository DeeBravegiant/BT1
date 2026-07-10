### Title
Pending Signature Requests Are Permanently Unfulfillable During `Initializing` State Due to Incomplete Protocol State Check in `respond` — (File: `crates/contract/src/lib.rs`)

---

### Summary

When the contract transitions from `Running` to `Initializing` via `vote_add_domains`, all in-flight signature requests for existing domains become unfulfillable. The `respond` function rejects calls unless the state is `Running` or `Resharing`, yet the `Initializing` state preserves the full existing keyset in `generated_keys` — exactly as `Resharing` preserves it in `previous_keyset()`. The missing `Initializing` branch causes every queued yield to time out, breaking the request-lifecycle invariant without any attacker action beyond the legitimate governance transition.

---

### Finding Description

**Root cause — incomplete state guard in `respond`**

`respond` (and its siblings `respond_ckd`, `respond_verify_foreign_tx`) gate execution on:

```rust
// crates/contract/src/lib.rs  ≈ line 575
if !self.protocol_state.is_running_or_resharing() {
    return Err(InvalidState::ProtocolStateNotRunning.into());
}
```

`is_running_or_resharing` is defined as:

```rust
// crates/contract/src/state.rs  line 215-220
pub fn is_running_or_resharing(&self) -> bool {
    matches!(
        self,
        ProtocolContractState::Running(_) | ProtocolContractState::Resharing(_)
    )
}
```

`Initializing` is not matched, so every `respond` call during that phase returns an error.

**The `Initializing` state carries valid existing keys**

`vote_add_domains` in `running.rs` transitions to `Initializing` by cloning the current keyset into `generated_keys`:

```rust
// crates/contract/src/state/running.rs  line 238-249
Ok(Some(InitializingContractState {
    generated_keys: self.keyset.domains.clone(),   // ← existing keys preserved
    domains: new_domains,
    epoch_id: self.keyset.epoch_id,
    generating_key: KeyEvent::new( ... ),
    cancel_votes: BTreeSet::new(),
}))
```

`InitializingContractState.generated_keys` therefore holds every key that was live in the preceding `Running` state. Signature verification for those domains is fully possible — the material is present — but `public_key()` returns an error for `Initializing`:

```rust
// crates/contract/src/state.rs  line 43-51
pub fn public_key(&self, domain_id: DomainId) -> Result<PublicKeyExtended, Error> {
    match self {
        ProtocolContractState::Running(state) => state.keyset.public_key(domain_id),
        ProtocolContractState::Resharing(state) => {
            state.previous_keyset().public_key(domain_id)
        }
        _ => Err(InvalidState::ProtocolStateNotRunningNorResharing.into()),
    }
}
```

**Contrast with `Resharing`**

`Resharing` is structurally identical: it also adds new key material while preserving the old keyset in `previous_running_state.keyset`. The contract correctly allows `respond` during `Resharing` and reads the old key via `previous_keyset()`. The `Initializing` case is the missed branch — the direct analog of the missing `DEPLOYED_STATUS` check in the bridge report.

**Lifecycle consequence**

Pending requests are stored as NEAR yield-resume promises in `pending_signature_requests`. Once the state enters `Initializing`, MPC nodes cannot call `respond` for any of those requests. The NEAR runtime will eventually time out each yield and invoke the failure callback, draining the queue entry via `pop_oldest_pending_yield`. The user's request is silently dropped; they must resubmit after the state returns to `Running`.

---

### Impact Explanation

Every signature, CKD, or foreign-transaction-verification request that was queued while the contract was `Running` becomes unfulfillable the moment `vote_add_domains` reaches unanimous consent and flips the state to `Initializing`. The `Initializing` phase can span many blocks (one key-generation round per new domain, each with potential timeouts and retries). During that window:

- All queued yields time out; callers receive a failure callback.
- Deposits (1 yoctoNEAR each) are not refunded by the failure path.
- Callers must resubmit, paying gas and deposit again.
- Any caller relying on the atomicity of "submit → receive signature" (e.g., a smart contract that cannot retry) is permanently broken for that request.

This breaks the production safety invariant: *a pending request for a domain whose key exists in contract state must be fulfillable by an honest node set*. The invariant holds for `Resharing` (correctly handled) but not for `Initializing` (missed branch).

Impact classification: **Medium** — request-lifecycle and contract execution-flow invariant violation that does not require network-level DoS or operator misconfiguration.

---

### Likelihood Explanation

`vote_add_domains` requires **all** current participants to cast the same vote (line 237 of `running.rs`: `self.parameters.participants().len() as u64 == n_votes`). This is a unanimous governance action, not a threshold one. It is a routine, expected operation whenever the network expands its signing capabilities (e.g., adding a new curve or CKD domain). Any production deployment that adds domains after launch will pass through this state. The window during which requests are stranded equals the total key-generation time for all new domains.

---

### Recommendation

Mirror the treatment of `Resharing` for the `Initializing` state:

1. **`is_running_or_resharing`** — rename or add a parallel predicate that also matches `Initializing`, or inline the check in `respond` to include `Initializing`.

2. **`public_key()`** — add an `Initializing` arm that looks up the domain in `generated_keys` (the already-generated prefix):

```rust
ProtocolContractState::Initializing(state) => {
    state.generated_keys
        .iter()
        .find(|kfd| kfd.domain_id == domain_id)
        .map(|kfd| kfd.key.clone())
        .ok_or_else(|| InvalidState::ProtocolStateNotRunningNorResharing.into())
}
```

3. **`domain_registry()`** — add an `Initializing` arm returning `&state.domains` so that `check_request_preconditions` can also validate domain existence during this phase.

---

### Proof of Concept

```
1. Contract is Running with domain D0 (key K0 in keyset).

2. User calls sign(domain_id = D0, payload = P)
   → stored in pending_signature_requests[R] = [YieldIndex{data_id}]

3. All N participants call vote_add_domains([D1])
   → unanimous vote reached (line 237, running.rs)
   → contract transitions to Initializing
   → InitializingContractState { generated_keys: [K0], ... }

4. MPC nodes finish computing σ = Sign(K0, P) and call respond(R, σ).

5. respond() checks is_running_or_resharing()
   → state is Initializing → returns false
   → respond returns Err(ProtocolStateNotRunning)          ← BUG

6. Nodes retry; every attempt fails identically.

7. NEAR runtime times out the yield for data_id.
   → return_signature_and_clean_state_on_success called with PromiseError
   → pop_oldest_pending_yield removes the entry
   → user's sign call resolves with an error; deposit is not refunded.

8. User must resubmit after Initializing → Running transition completes.
   Any non-retryable caller (e.g., a contract) permanently loses the request.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** crates/contract/src/lib.rs (L575-577)
```rust
        if !self.protocol_state.is_running_or_resharing() {
            return Err(InvalidState::ProtocolStateNotRunning.into());
        }
```

**File:** crates/contract/src/state.rs (L43-51)
```rust
    pub fn public_key(&self, domain_id: DomainId) -> Result<PublicKeyExtended, Error> {
        match self {
            ProtocolContractState::Running(state) => state.keyset.public_key(domain_id),
            ProtocolContractState::Resharing(state) => {
                state.previous_keyset().public_key(domain_id)
            }
            _ => Err(InvalidState::ProtocolStateNotRunningNorResharing.into()),
        }
    }
```

**File:** crates/contract/src/state.rs (L215-220)
```rust
    pub fn is_running_or_resharing(&self) -> bool {
        matches!(
            self,
            ProtocolContractState::Running(_) | ProtocolContractState::Resharing(_)
        )
    }
```

**File:** crates/contract/src/state/running.rs (L237-249)
```rust
        if self.parameters.participants().len() as u64 == n_votes {
            let new_domains = self.domains.add_domains(domains.clone())?;
            Ok(Some(InitializingContractState {
                generated_keys: self.keyset.domains.clone(),
                domains: new_domains,
                epoch_id: self.keyset.epoch_id,
                generating_key: KeyEvent::new(
                    self.keyset.epoch_id,
                    domains[0].clone(),
                    self.parameters.clone(),
                ),
                cancel_votes: BTreeSet::new(),
            }))
```

**File:** crates/contract/src/state/initializing.rs (L30-43)
```rust
pub struct InitializingContractState {
    /// All domains, including the already existing ones and the ones we're generating a new key for
    pub domains: DomainRegistry,
    /// The epoch ID; this is the same as the Epoch ID of the Running state we transitioned from.
    pub epoch_id: EpochId,
    /// The key for each domain we have already generated a key for; this is in the same order as
    /// the domains in the DomainRegistry, except that it only has a prefix of the domains.
    pub generated_keys: Vec<KeyForDomain>,
    /// The key generation state for the currently generating domain (the next domain after
    /// `generated_keys`).
    pub generating_key: KeyEvent,
    /// Votes that have been cast to cancel the key generation.
    pub cancel_votes: BTreeSet<AuthenticatedParticipantId>,
}
```

**File:** crates/contract/src/pending_requests.rs (L97-111)
```rust
pub(crate) fn pop_oldest_pending_yield<K>(requests: &mut LookupMap<K, Vec<YieldIndex>>, request: &K)
where
    K: BorshSerialize + BorshDeserialize + Clone + Ord,
{
    let Some(queue) = requests.get_mut(request) else {
        return;
    };
    if queue.is_empty() {
        requests.remove(request);
        return;
    }
    queue.remove(0);
    if queue.is_empty() {
        requests.remove(request);
    }
```
