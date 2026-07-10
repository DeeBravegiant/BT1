### Title
Caller-Agnostic `verify_foreign_transaction` Queue Key Enables Adversarial Queue Saturation to Permanently Block Bridge Fund Release - (File: `crates/contract/src/lib.rs`, `crates/contract/src/pending_requests.rs`)

### Summary

The `verify_foreign_transaction` endpoint uses a caller-agnostic request key (`VerifyForeignTransactionRequest` = `{request, domain_id, payload_version}`) for its pending-request fan-out queue. Any unprivileged caller can fill the 128-entry queue for a specific foreign transaction ID at near-zero cost (1 yoctoNEAR per entry), permanently blocking any bridge contract from submitting a verification request for that transaction. The adversary can maintain the queue at capacity indefinitely by re-submitting as entries time out, locking bridge user funds.

### Finding Description

`verify_foreign_transaction` converts user arguments into a `VerifyForeignTransactionRequest` that deliberately omits the caller's account ID:

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

This caller-agnostic key is then used as the map key in `pending_verify_foreign_tx_requests`. The `push_pending_yield` function enforces a hard cap of `MAX_PENDING_REQUEST_FAN_OUT = 128` entries per key, panicking with `PendingRequestQueueFull` when exceeded:

```rust
pub const MAX_PENDING_REQUEST_FAN_OUT: u8 = 128;

pub(crate) fn push_pending_yield<K>(...) {
    let queue = requests.entry(request).or_default();
    if queue.len() >= usize::from(MAX_PENDING_REQUEST_FAN_OUT) {
        env::panic_str(
            &RequestError::PendingRequestQueueFull { limit: MAX_PENDING_REQUEST_FAN_OUT }.to_string(),
        );
    }
    queue.push(YieldIndex { data_id });
}
```

The cap was introduced to prevent gas exhaustion in `respond_verify_foreign_tx` (which drains the entire queue in one call). However, the cap itself creates a new attack surface: an adversary can saturate the queue for any specific `(chain_request, domain_id, payload_version)` tuple, blocking all other callers from submitting that same request.

This is structurally identical to the H-4 AutoRoller bug: just as an adversary pre-creates a series at the target maturity to brick the AutoRoller, here an adversary pre-fills the queue for a target foreign tx_id to brick any bridge contract waiting to verify that transaction.

Contrast with `sign`, where the key includes the predecessor account ID (`SignatureRequest::new(domain_id, payload, &predecessor, &path)`), making cross-user queue saturation impossible. `verify_foreign_transaction` has no such protection.

The developers' own test comment acknowledges the caller-agnostic design:
> "a different account would today be blocked from receiving a response by alice's submission"

### Impact Explanation

For the primary production use case (Omnibridge inbound flow):

1. A user sends funds on Bitcoin to a bridge address (funds are now committed on the foreign chain).
2. The bridge contract on NEAR calls `verify_foreign_transaction(Bitcoin tx_id=X)` to release equivalent NEAR-side funds.
3. An adversary submits 128 identical `verify_foreign_transaction(tx_id=X)` calls (cost: 128 yoctoNEAR ≈ $0).
4. The bridge contract's call panics with `PendingRequestQueueFull`.
5. As entries time out (FIFO, one at a time), the adversary immediately re-submits to keep the queue at 128.
6. The bridge contract can never submit its verification request; the user's funds are permanently locked on the foreign chain.

This breaks the production safety invariant of the bridge: funds sent on the foreign chain must be releasable on NEAR. The adversary can maintain the attack indefinitely at near-zero cost.

### Likelihood Explanation

- **Entry barrier**: Any NEAR account with 1 yoctoNEAR per call can execute this attack. No special privileges, key material, or threshold collusion required.
- **Cost**: 128 yoctoNEAR to fill the queue; re-submission cost as entries time out is negligible.
- **Targeting**: The adversary only needs to know the target `tx_id` (publicly visible on the foreign chain) and the `domain_id` (publicly readable from contract state).
- **Sustainability**: The adversary can automate re-submission to maintain the queue at capacity indefinitely.
- **Motivation**: Competitors, bridge attackers, or anyone wishing to lock a specific user's bridge funds.

### Recommendation

Include the caller's `predecessor_account_id` in the `VerifyForeignTransactionRequest` key, mirroring the `sign` endpoint's design. This ensures each caller has an independent queue slot that cannot be saturated by other accounts.

Alternatively, if caller-agnostic fan-out is intentional (to allow multiple bridge contracts to share a single MPC computation for the same tx), introduce a per-caller sub-queue or a per-caller submission limit so that no single account can consume more than a bounded fraction of the 128-entry cap.

### Proof of Concept

```
// Adversary fills the queue for Bitcoin tx_id=X to 128 entries
for i in 0..128 {
    adversary.call(contract, "verify_foreign_transaction")
        .args_json({ "request": { "request": { "Bitcoin": { "tx_id": X, ... } }, "domain_id": 0, "payload_version": "V1" } })
        .deposit(1 yoctoNEAR)
        .transact();
}

// Bridge contract's call now panics with PendingRequestQueueFull
bridge.call(contract, "verify_foreign_transaction")
    .args_json({ "request": { "request": { "Bitcoin": { "tx_id": X, ... } }, "domain_id": 0, "payload_version": "V1" } })
    .deposit(1 yoctoNEAR)
    .transact();
// => PANIC: "Pending-request queue is full for this request key (limit: 128)."

// Adversary monitors timeouts and re-submits to maintain queue at 128,
// permanently blocking the bridge contract from verifying tx_id=X.
```

**Root cause references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** crates/contract/src/lib.rs (L379-384)
```rust
        let request = SignatureRequest::new(
            request.domain_id,
            request.payload,
            &predecessor,
            &request.path,
        );
```

**File:** crates/contract/src/lib.rs (L519-557)
```rust
    pub fn verify_foreign_transaction(&mut self, request: VerifyForeignTransactionRequestArgs) {
        log!(
            "verify_foreign_transaction: predecessor={:?}, request={:?}",
            env::predecessor_account_id(),
            request
        );

        self.check_request_preconditions(
            request.domain_id,
            DomainPurpose::ForeignTx,
            Gas::from_tgas(self.config.sign_call_gas_attachment_requirement_tera_gas),
            MINIMUM_SIGN_REQUEST_DEPOSIT,
        );

        let requested_chain = request.request.chain();
        let supported_chains = self.get_supported_foreign_chains();
        if !supported_chains.contains(&requested_chain) {
            env::panic_str(
                &InvalidParameters::ForeignChainNotSupported {
                    requested: requested_chain,
                }
                .to_string(),
            );
        }

        let callback_gas = Gas::from_tgas(
            self.config
                .return_signature_and_clean_state_on_success_call_tera_gas,
        );

        let request = args_into_verify_foreign_tx_request(request);
        let callback_args = serde_json::to_vec(&(&request,)).unwrap();
        self.enqueue_yield_request(
            method_names::RETURN_VERIFY_FOREIGN_TX_AND_CLEAN_STATE_ON_SUCCESS,
            callback_args,
            callback_gas,
            move |this, id| this.add_verify_foreign_tx_request(request, id),
        );
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
