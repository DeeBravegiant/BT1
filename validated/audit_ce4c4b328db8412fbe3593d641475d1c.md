### Title
Caller-Agnostic `verify_foreign_transaction` Queue Key Enables Unprivileged Fan-Out Saturation DoS — (`File: crates/contract/src/lib.rs`, `crates/contract/src/pending_requests.rs`)

---

### Summary

The `verify_foreign_transaction` endpoint uses a request key that does **not** include the caller's account ID. Any unprivileged account can submit 128 identical `verify_foreign_transaction` calls for a specific foreign-chain transaction ID, saturating the per-key fan-out queue (`MAX_PENDING_REQUEST_FAN_OUT = 128`) and causing every subsequent legitimate caller targeting the same transaction to receive `PendingRequestQueueFull`. By continuously front-running the bridge service's retry attempts, an attacker can sustain this denial-of-service indefinitely at negligible cost (~128 yoctoNEAR + gas per cycle).

---

### Finding Description

**Root cause — caller-agnostic request key for `verify_foreign_transaction`:**

The `sign()` function constructs a `SignatureRequest` that embeds the caller's account ID via `derive_tweak(&predecessor, &path)`:

```rust
let request = SignatureRequest::new(
    request.domain_id,
    request.payload,
    &predecessor,   // ← caller is part of the key
    &request.path,
);
```

This means two different callers submitting the same payload produce **different** map keys, so one caller cannot fill another's queue.

`verify_foreign_transaction()` does the opposite. The conversion function `args_into_verify_foreign_tx_request` strips the caller entirely:

```rust
pub fn args_into_verify_foreign_tx_request(
    args: dtos::VerifyForeignTransactionRequestArgs,
) -> dtos::VerifyForeignTransactionRequest {
    dtos::VerifyForeignTransactionRequest {
        domain_id: args.domain_id,
        request: args.request,
        payload_version: args.payload_version,
        // ← no predecessor_id, no tweak
    }
}
```

The resulting `VerifyForeignTransactionRequest` struct contains only `{request, domain_id, payload_version}`:

```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```

This struct is used directly as the map key in `pending_verify_foreign_tx_requests`. The codebase itself acknowledges this in a test comment: *"caller bob submits the identical request — a different account would today be blocked from receiving a response by alice's submission."*

**Fan-out queue cap creates the saturation vector:**

`pending_requests.rs` caps the queue at `MAX_PENDING_REQUEST_FAN_OUT = 128` to prevent gas exhaustion in `respond_verify_foreign_tx`. Once the queue is full, `push_pending_yield` panics with `PendingRequestQueueFull`:

```rust
if queue.len() >= usize::from(MAX_PENDING_REQUEST_FAN_OUT) {
    env::panic_str(
        &RequestError::PendingRequestQueueFull {
            limit: MAX_PENDING_REQUEST_FAN_OUT,
        }
        .to_string(),
    );
}
```

**Attack flow:**

1. Attacker observes a large inbound bridge transfer on Bitcoin/Ethereum (tx_id is public).
2. Attacker submits 128 `verify_foreign_transaction` calls with the same `{tx_id, domain_id, payload_version}` from one or more accounts. Each call costs 1 yoctoNEAR + ~10 TGas.
3. The queue for that request key is now full.
4. The bridge service's `verify_foreign_transaction` call for the same tx_id is rejected with `PendingRequestQueueFull`.
5. MPC nodes eventually respond, draining the attacker's 128 yields. The attacker receives 128 copies of the verification response (harmless to them).
6. Attacker immediately re-fills the queue before the bridge service can retry.
7. The bridge service is permanently blocked from verifying that specific transaction.

---

### Impact Explanation

The bridge service (e.g., Omnibridge inbound flow) cannot obtain a signed attestation for a specific foreign-chain transaction. If the foreign chain has a time-sensitive claim window (e.g., a lock expiry), user funds on the foreign chain may become permanently inaccessible. Even without a time window, the bridge's inbound flow is broken for the targeted transaction, breaking the production safety invariant that any finalized foreign-chain transaction can be verified and attested by the MPC network.

This is a **request-lifecycle manipulation that breaks contract execution-flow** for the bridge integration — matching the Medium allowed impact: *"Balance, request-lifecycle, participant-state, or contract execution-flow manipulation that breaks production safety/accounting invariants without relying on network-level DoS or operator misconfiguration."*

---

### Likelihood Explanation

- **No special privileges required**: any NEAR account can call `verify_foreign_transaction` with a 1 yoctoNEAR deposit.
- **Cost is negligible**: 128 calls × (1 yoctoNEAR + ~10 TGas gas) ≈ 0.13 NEAR (~$0.40 at current prices) per attack cycle.
- **Target is observable**: foreign-chain transaction IDs are public; an attacker can monitor mempool or block explorers to identify high-value bridge transactions to target.
- **Sustained attack is trivial**: the attacker simply re-fills the queue after each MPC response drains it. The NEAR yield-resume timeout (~200 blocks ≈ 4 minutes) sets the re-fill cadence.
- **No honest-node collusion needed**: the attack is entirely on-chain contract state.

---

### Recommendation

1. **Include the caller's account ID in the `VerifyForeignTransactionRequest` key**, analogous to how `sign()` includes the predecessor via `derive_tweak`. This prevents one caller from filling another's queue slot. The trade-off is that two callers requesting the same foreign-tx verification no longer share a single MPC computation — but this is the correct security boundary.

2. **Alternatively**, if caller-agnostic fan-out is intentional (to amortize MPC computation across multiple bridge callers), introduce a **per-caller sub-queue** within the fan-out map, so each caller can hold at most N slots regardless of what other callers do.

3. **Increase the minimum deposit** for `verify_foreign_transaction` to make queue saturation economically infeasible.

---

### Proof of Concept

The existing unit test `verify_foreign_transaction__should_queue_duplicates_from_different_callers` in `crates/contract/src/lib.rs` already demonstrates the caller-agnostic key behavior. Extending it to 128 callers demonstrates the saturation:

```rust
// Attacker fills the queue with 128 identical requests
for i in 0..MAX_PENDING_REQUEST_FAN_OUT {
    let attacker = AccountId::from_str(&format!("attacker{}.near", i)).unwrap();
    testing_env!(VMContextBuilder::new()
        .predecessor_account_id(attacker)
        .attached_deposit(NearToken::from_yoctonear(1))
        .build());
    contract.verify_foreign_transaction(request_args.clone());
}

// Queue is now full — legitimate bridge caller is rejected
let bridge = AccountId::from_str("bridge.near").unwrap();
testing_env!(VMContextBuilder::new()
    .predecessor_account_id(bridge)
    .attached_deposit(NearToken::from_yoctonear(1))
    .build());
// This panics with PendingRequestQueueFull:
contract.verify_foreign_transaction(request_args.clone());
```

**Key code references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** crates/contract/src/dto_mapping.rs (L840-848)
```rust
pub fn args_into_verify_foreign_tx_request(
    args: dtos::VerifyForeignTransactionRequestArgs,
) -> dtos::VerifyForeignTransactionRequest {
    dtos::VerifyForeignTransactionRequest {
        domain_id: args.domain_id,
        request: args.request,
        payload_version: args.payload_version,
    }
}
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L124-128)
```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```

**File:** crates/contract/src/lib.rs (L379-384)
```rust
        let request = SignatureRequest::new(
            request.domain_id,
            request.payload,
            &predecessor,
            &request.path,
        );
```

**File:** crates/contract/src/lib.rs (L549-556)
```rust
        let request = args_into_verify_foreign_tx_request(request);
        let callback_args = serde_json::to_vec(&(&request,)).unwrap();
        self.enqueue_yield_request(
            method_names::RETURN_VERIFY_FOREIGN_TX_AND_CLEAN_STATE_ON_SUCCESS,
            callback_args,
            callback_gas,
            move |this, id| this.add_verify_foreign_tx_request(request, id),
        );
```

**File:** crates/contract/src/lib.rs (L3242-3243)
```rust
        // And: caller bob submits the identical request — a different account would today
        // be blocked from receiving a response by alice's submission.
```

**File:** crates/contract/src/pending_requests.rs (L24-59)
```rust
/// Maximum number of concurrent yield-resume promises that can be queued for a single
/// request key (i.e. the number of duplicate submissions whose responses fan out from
/// one MPC reply).
///
/// The ceiling is needed because `respond*` drains the entire queue in one call: every
/// queued yield triggers a host-side `promise_yield_resume`, paid for out of the
/// responder's 300 TGas budget. Without a cap, an attacker could enqueue enough
/// duplicates to make `respond*` run out of gas and strand every queued caller.
///
/// 128 is validated empirically by the sandbox test
/// `test_contract_request_duplicate_requests_fan_out`, which fills the queue to this
/// cap across all four signature schemes and confirms `respond*` drains it inside its
/// 300 TGas budget.
pub const MAX_PENDING_REQUEST_FAN_OUT: u8 = 128;

/// Append a yield index to the pending-request fan-out queue for `request`.
///
/// Panics with `RequestError::PendingRequestQueueFull` if the resulting queue would
/// exceed `MAX_PENDING_REQUEST_FAN_OUT`.
pub(crate) fn push_pending_yield<K>(
    requests: &mut LookupMap<K, Vec<YieldIndex>>,
    request: K,
    data_id: CryptoHash,
) where
    K: BorshSerialize + BorshDeserialize + Clone + Ord,
{
    let queue = requests.entry(request).or_default();
    if queue.len() >= usize::from(MAX_PENDING_REQUEST_FAN_OUT) {
        env::panic_str(
            &RequestError::PendingRequestQueueFull {
                limit: MAX_PENDING_REQUEST_FAN_OUT,
            }
            .to_string(),
        );
    }
    queue.push(YieldIndex { data_id });
```
