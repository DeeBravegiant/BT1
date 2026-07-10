### Title
Caller-Agnostic `verify_foreign_transaction` Request Key Allows Unprivileged Queue Saturation DOS - (File: `crates/contract/src/pending_requests.rs`, `crates/contract/src/lib.rs`)

---

### Summary

The `verify_foreign_transaction` endpoint uses a caller-agnostic request key (`VerifyForeignTransactionRequest` = `{request, domain_id, payload_version}` — no predecessor/caller field). Combined with the hard fan-out cap of `MAX_PENDING_REQUEST_FAN_OUT = 128`, any unprivileged NEAR account can pre-fill the queue for a specific foreign transaction ID, causing every subsequent legitimate submission for that same transaction to panic with `PendingRequestQueueFull`. This blocks bridge services from obtaining the MPC-signed verification they need to release funds on the foreign chain.

---

### Finding Description

`push_pending_yield` enforces a hard cap of 128 yields per request key:

```rust
pub const MAX_PENDING_REQUEST_FAN_OUT: u8 = 128;

pub(crate) fn push_pending_yield<K>(...) {
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
}
``` [1](#0-0) 

The `VerifyForeignTransactionRequest` struct — which is the map key — contains no caller identity:

```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
``` [2](#0-1) 

The `verify_foreign_transaction` handler converts the user-supplied args directly into this caller-agnostic key and enqueues a yield under it: [3](#0-2) 

By contrast, `sign()` binds the predecessor account into `SignatureRequest::new(domain_id, payload, &predecessor, &path)`, making each user's queue independent. `verify_foreign_transaction` has no such binding.

The codebase's own test comment acknowledges the asymmetry:

> "caller bob submits the identical request — a different account would today be blocked from receiving a response by alice's submission." [4](#0-3) 

---

### Impact Explanation

An attacker who observes a target foreign transaction ID (e.g., a Bitcoin `tx_id` that a bridge service is about to verify) can submit 128 `verify_foreign_transaction` calls for that exact `{tx_id, domain_id, payload_version}` tuple before the bridge service does. Once the queue is saturated, every subsequent call from the legitimate bridge service panics with `PendingRequestQueueFull`, and the bridge service cannot obtain the MPC-signed verification response needed to release funds on the foreign chain.

The attacker's 128 queued yields time out after `REQUEST_EXPIRATION_BLOCKS = 200` blocks (~3–4 minutes on NEAR), at which point the queue clears — but the attacker can immediately repeat the attack. The cost is 128 × 1 yoctoNEAR deposit plus gas, making sustained suppression of a specific transaction economically trivial.

This breaks the production safety invariant that any user who pays the required deposit and submits a valid foreign-chain transaction ID can obtain a signed verification. It maps to: **Medium — request-lifecycle manipulation that breaks production safety/accounting invariants without relying on network-level DoS or operator misconfiguration.** [5](#0-4) 

---

### Likelihood Explanation

The attack requires no special privilege — only a funded NEAR account and knowledge of the target `tx_id`, which is public on the foreign chain. A bridge operator or competitor who wants to suppress a specific cross-chain withdrawal can execute this attack repeatedly at negligible cost. The 200-block timeout window is short enough that the attacker can maintain continuous suppression with automated tooling. [6](#0-5) 

---

### Recommendation

Bind the caller's predecessor account ID into the `VerifyForeignTransactionRequest` key, mirroring the approach used by `SignatureRequest`. This makes each caller's queue independent, so an attacker can only saturate their own queue, not another user's. Alternatively, enforce a per-account submission rate limit at the contract level before the yield is enqueued. [7](#0-6) 

---

### Proof of Concept

1. Bridge service is about to call `verify_foreign_transaction({Bitcoin, tx_id: 0xABCD..., confirmations: 6, extractors: [BlockHash]}, domain_id: 0, payload_version: V1)`.
2. Attacker observes `tx_id = 0xABCD...` on Bitcoin mempool/chain.
3. Attacker submits 128 identical `verify_foreign_transaction` calls with the same args, each with 1 yoctoNEAR deposit. Total cost: 128 yoctoNEAR + gas.
4. `pending_verify_foreign_tx_requests[{Bitcoin, 0xABCD..., V1}]` now holds 128 `YieldIndex` entries.
5. Bridge service submits its `verify_foreign_transaction` call → `push_pending_yield` checks `queue.len() >= 128` → `env::panic_str("Pending-request queue is full for this request key (limit: 128).")` → bridge service's transaction fails.
6. After 200 blocks, all 128 attacker yields time out via `pop_oldest_pending_yield` and the queue empties.
7. Attacker repeats from step 3, maintaining indefinite suppression of this specific transaction's verification. [8](#0-7) [9](#0-8)

### Citations

**File:** crates/contract/src/pending_requests.rs (L37-59)
```rust
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

**File:** crates/contract/src/lib.rs (L3300-3344)
```rust
    #[test]
    fn add_signature_request__should_panic_when_pending_queue_is_full() {
        // Given: a contract with a queue already at the fan-out cap for some request key.
        let (context, mut contract, _) = basic_setup(Curve::Secp256k1, &mut OsRng);
        let signature_request = SignatureRequest::new(
            DomainId::default(),
            Payload::from_legacy_ecdsa([3u8; 32]),
            &context.predecessor_account_id,
            "m/44'\''/60'\''/0'\''/0/0",
        );
        for i in 0..MAX_PENDING_REQUEST_FAN_OUT {
            contract.add_signature_request(signature_request.clone(), [i; 32]);
        }
        assert_eq!(
            contract
                .pending_signature_requests
                .get(&signature_request)
                .map(|q| q.len()),
            Some(usize::from(MAX_PENDING_REQUEST_FAN_OUT)),
        );

        // When: one more append is attempted.
        let result = panic::catch_unwind(panic::AssertUnwindSafe(|| {
            contract.add_signature_request(signature_request.clone(), [0xff; 32]);
        }));

        // Then: it panics with the typed cap-exceeded error and leaves the queue untouched.
        let err = result.expect_err("appending past the cap should panic");
        let msg = err
            .downcast_ref::<String>()
            .map(String::as_str)
            .or_else(|| err.downcast_ref::<&str>().copied())
            .unwrap_or_default();
        assert!(
            msg.contains("Pending-request queue is full"),
            "unexpected panic message: {msg}",
        );
        assert_eq!(
            contract
                .pending_signature_requests
                .get(&signature_request)
                .map(|q| q.len()),
            Some(usize::from(MAX_PENDING_REQUEST_FAN_OUT)),
            "queue should not have grown past the cap",
        );
```

**File:** crates/contract/src/errors.rs (L37-41)
```rust
    #[error(
        "Pending-request queue is full for this request key (limit: {limit}). Try again once an in-flight response or timeout has cleared room."
    )]
    PendingRequestQueueFull { limit: u8 },
}
```

**File:** crates/node/src/requests/queue.rs (L33-33)
```rust
pub const REQUEST_EXPIRATION_BLOCKS: NumBlocks = 200;
```
