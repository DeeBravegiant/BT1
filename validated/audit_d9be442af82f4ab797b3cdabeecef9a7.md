### Title
Unprivileged Caller Can Fill `verify_foreign_transaction` Pending Queue with Unvalidated Requests, Blocking Legitimate Foreign-Chain Verification - (File: `crates/contract/src/lib.rs`, `crates/contract/src/pending_requests.rs`)

### Summary

The `verify_foreign_transaction` contract method enqueues requests under a key that does not include the caller's account ID. Any unprivileged account can submit up to `MAX_PENDING_REQUEST_FAN_OUT` (128) requests for any specific foreign-chain transaction ID — including a non-existent one — filling the queue for that key. Once full, every subsequent `verify_foreign_transaction` call for the same parameters panics with `PendingRequestQueueFull`, blocking legitimate bridge users from verifying that transaction until all attacker-submitted yields time out.

### Finding Description

**Root cause — caller-agnostic queue key for `verify_foreign_transaction`:**

`verify_foreign_transaction` converts the user-supplied args into a `VerifyForeignTransactionRequest` and enqueues a yield under that struct as the map key: [1](#0-0) 

The internal `VerifyForeignTransactionRequest` type contains only `domain_id`, `payload_version`, and `request` (chain, `tx_id`, extractors). It does **not** include the caller's `predecessor_account_id`: [2](#0-1) 

This is explicitly confirmed by the test `verify_foreign_transaction__should_queue_duplicates_from_different_callers`, which documents that Alice and Bob share the same queue entry for identical request parameters: [3](#0-2) 

**Contrast with `sign` and `request_app_private_key`:** Both of those functions include `&predecessor` (the caller's account ID) when constructing their request key, so each caller has an isolated queue: [4](#0-3) 

**The cap and its exploitation:**

`pending_requests.rs` caps the fan-out queue at 128 entries per key. When the cap is reached, the contract panics: [5](#0-4) 

**Attack path:**

1. Attacker observes (or anticipates) a legitimate `verify_foreign_transaction` call for a specific `(chain, tx_id, extractors, domain_id)` tuple — e.g., a Bitcoin tx being bridged via Omnibridge.
2. Attacker front-runs with 128 calls to `verify_foreign_transaction` using the same parameters (or a fake `tx_id` that matches the target key). Each call costs only 1 yoctoNEAR deposit.
3. The queue for that key is now full.
4. The legitimate user's call panics with `PendingRequestQueueFull`.
5. MPC nodes attempt to verify the attacker's (possibly non-existent) tx, fail, and the yields time out after ~200 blocks each. Because all 128 were submitted simultaneously, they all time out together (~200 blocks ≈ 4 minutes), after which the attacker can immediately refill the queue.
6. The attacker sustains the block indefinitely at negligible cost (128 × 1 yoctoNEAR per ~200-block cycle).

The contract performs no validation of whether the `tx_id` actually exists on the foreign chain before enqueuing: [6](#0-5) 

### Impact Explanation

This is a **Medium** impact finding matching the allowed scope: *"Balance, request-lifecycle, participant-state, or contract execution-flow manipulation that breaks production safety/accounting invariants without relying on network-level DoS or operator misconfiguration."*

A legitimate bridge user (e.g., an Omnibridge inbound flow relying on `verify_foreign_transaction` to attest a foreign-chain deposit) is permanently blocked from completing their verification for any specific transaction the attacker targets. The attacker can sustain the block indefinitely at negligible cost. This breaks the production invariant that any user can submit a foreign-chain verification request for a supported chain.

### Likelihood Explanation

The attack is reachable by any unprivileged NEAR account. The cost is 128 yoctoNEAR per ~4-minute blocking window. The attacker needs only to know the target `(chain, tx_id, extractors, domain_id)` tuple, which is observable on-chain from the mempool or block history. Front-running is straightforward on NEAR. Likelihood is **Medium-High** for any high-value bridge operation.

### Recommendation

Include the caller's `predecessor_account_id` in the `VerifyForeignTransactionRequest` key, mirroring the pattern used by `sign` and `request_app_private_key`. This gives each caller an isolated queue slot and prevents one account from filling the shared queue for a given foreign transaction.

Alternatively, enforce a per-caller rate limit or per-caller queue cap for `verify_foreign_transaction` requests, so a single account cannot consume more than a bounded fraction of the global fan-out budget for any given request key.

### Proof of Concept

```
// Attacker account: attacker.near
// Target: legitimate user alice.near wants to verify Bitcoin tx_id=[0xAB; 32]

// Step 1: Attacker submits 128 identical verify_foreign_transaction calls
for i in 0..128 {
    attacker.near -> mpc_contract.verify_foreign_transaction({
        domain_id: <ForeignTx domain>,
        payload_version: V1,
        request: Bitcoin { tx_id: [0xAB; 32], confirmations: 1, extractors: [BlockHash] }
    }, deposit: 1 yoctoNEAR)
}
// Queue for key (Bitcoin, [0xAB;32], 1, [BlockHash], domain) is now full (128/128)

// Step 2: Alice submits her legitimate request
alice.near -> mpc_contract.verify_foreign_transaction({
    domain_id: <ForeignTx domain>,
    payload_version: V1,
    request: Bitcoin { tx_id: [0xAB; 32], confirmations: 1, extractors: [BlockHash] }
}, deposit: 1 yoctoNEAR)
// PANICS: "Pending-request queue is full for this request key (limit: 128)"

// Step 3: After ~200 blocks, attacker refills the queue. Alice remains blocked.
```

The `PendingRequestQueueFull` panic is confirmed at: [7](#0-6)

### Citations

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

**File:** crates/contract/src/lib.rs (L3255-3263)
```rust
        // Then: both yields are queued under the single (caller-agnostic) request key.
        assert_eq!(
            contract
                .pending_verify_foreign_tx_requests
                .get(&request)
                .map(|q| q.len()),
            Some(2),
            "duplicate foreign-tx requests from different callers should fan out",
        );
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L101-105)
```rust
pub struct VerifyForeignTransactionRequestArgs {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```

**File:** crates/contract/src/pending_requests.rs (L37-58)
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
```

**File:** crates/contract/src/errors.rs (L37-41)
```rust
    #[error(
        "Pending-request queue is full for this request key (limit: {limit}). Try again once an in-flight response or timeout has cleared room."
    )]
    PendingRequestQueueFull { limit: u8 },
}
```
