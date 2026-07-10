### Title
Caller-Agnostic `verify_foreign_transaction` Fan-Out Queue Exhaustion Enables Targeted Request-Lifecycle DoS - (File: `crates/contract/src/pending_requests.rs`)

### Summary

The `verify_foreign_transaction` request key does not include the caller's account ID, making the 128-slot fan-out queue per request key exhaustible by any unprivileged account. An attacker can fill the queue for any specific foreign transaction at negligible cost (128 × 1 yoctoNEAR), blocking all other users from queuing that same request for up to 200 blocks per cycle, repeatable indefinitely.

### Finding Description

The `pending_requests` module enforces a hard cap of `MAX_PENDING_REQUEST_FAN_OUT = 128` concurrent yield-resume slots per request key. [1](#0-0) 

When the queue is full, `push_pending_yield` panics with `PendingRequestQueueFull`, rejecting all further submissions for that key. [2](#0-1) 

For `sign` requests, the key is constructed with the caller's `predecessor` account ID, so different callers cannot fill each other's queues: [3](#0-2) 

However, `VerifyForeignTransactionRequest` — the map key for `pending_verify_foreign_tx_requests` — contains only `{request, domain_id, payload_version}` with **no caller field**: [4](#0-3) 

The `verify_foreign_transaction` handler converts the user args to this caller-agnostic key before enqueuing: [5](#0-4) 

The codebase itself acknowledges this property in a test comment: *"a different account would today be blocked from receiving a response by alice's submission"*: [6](#0-5) 

An attacker submits 128 `verify_foreign_transaction` calls for the same `(tx_id, domain_id, payload_version)` tuple. The queue is now full. Every subsequent legitimate submission for that foreign transaction is rejected with `PendingRequestQueueFull`. The queue clears only after the 200-block yield-resume timeout expires per slot: [7](#0-6) 

The attacker can immediately re-fill the queue after each timeout cycle, sustaining the block indefinitely.

### Impact Explanation

This is a **Medium** request-lifecycle manipulation. Any user relying on `verify_foreign_transaction` for a specific foreign chain transaction (e.g., a bridge settlement, a cross-chain proof) is denied service for the duration of the attack. The production safety invariant — that any user can submit a foreign transaction verification and receive a response — is broken without requiring operator misconfiguration or network-level DoS. The attacker does not need to be a participant or hold any privileged role.

### Likelihood Explanation

The attack requires only a NEAR account and 128 × 1 yoctoNEAR (effectively free). The attacker needs to know the target `tx_id` and `domain_id`, which are observable on-chain. The attack is repeatable every ~200 blocks (~200 seconds on NEAR mainnet). No threshold collusion, TEE access, or key material is required.

### Recommendation

Include the caller's `predecessor_account_id` in the `VerifyForeignTransactionRequest` key (analogous to how `SignatureRequest::new` incorporates the predecessor), or implement a per-account submission cap per request key. If caller-agnostic fan-out is intentional (to allow multiple callers to share one MPC round-trip), consider a per-account slot reservation within the 128-slot budget so a single account cannot exhaust the entire queue.

### Proof of Concept

```
1. Eve observes that Alice is about to submit verify_foreign_transaction for
   Bitcoin tx_id=0xABCD..., domain_id=1, payload_version=V1.

2. Eve submits 128 identical verify_foreign_transaction calls with the same
   (tx_id, domain_id, payload_version) from her account, each with 1 yoctoNEAR.
   Total cost: 128 yoctoNEAR ≈ $0.

3. pending_verify_foreign_tx_requests[key].len() == 128 (queue full).

4. Alice submits verify_foreign_transaction with the same args.
   → Contract panics: "Pending-request queue is full for this request key (limit: 128)."
   → Alice's transaction fails; she receives no response.

5. After ~200 blocks, Eve's slots time out via return_verify_foreign_tx_and_clean_state_on_success.
   Eve immediately re-submits 128 calls, repeating the cycle indefinitely.
``` [8](#0-7) [9](#0-8)

### Citations

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

**File:** crates/contract/src/lib.rs (L2316-2337)
```rust
    pub fn return_verify_foreign_tx_and_clean_state_on_success(
        &mut self,
        request: VerifyForeignTransactionRequest,
        #[callback_result] response: Result<VerifyForeignTransactionResponse, PromiseError>,
    ) -> PromiseOrValue<VerifyForeignTransactionResponse> {
        match response {
            Ok(response) => PromiseOrValue::Value(response),
            Err(_) => {
                pending_requests::pop_oldest_pending_yield(
                    &mut self.pending_verify_foreign_tx_requests,
                    &request,
                );
                let fail_on_timeout_gas = Gas::from_tgas(self.config.fail_on_timeout_tera_gas);
                let promise = Promise::new(env::current_account_id()).function_call(
                    method_names::FAIL_ON_TIMEOUT.to_string(),
                    vec![],
                    NearToken::from_near(0),
                    fail_on_timeout_gas,
                );
                near_sdk::PromiseOrValue::Promise(promise.as_return())
            }
        }
```

**File:** crates/contract/src/lib.rs (L3242-3243)
```rust
        // And: caller bob submits the identical request — a different account would today
        // be blocked from receiving a response by alice's submission.
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L124-128)
```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```

**File:** crates/node/src/requests/queue.rs (L31-33)
```rust
/// The number of blocks after which a request is assumed to have timed out.
/// This is equal to the yield-resume timeout on the blockchain.
pub const REQUEST_EXPIRATION_BLOCKS: NumBlocks = 200;
```

**File:** crates/contract/src/errors.rs (L37-41)
```rust
    #[error(
        "Pending-request queue is full for this request key (limit: {limit}). Try again once an in-flight response or timeout has cleared room."
    )]
    PendingRequestQueueFull { limit: u8 },
}
```
