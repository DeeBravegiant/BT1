### Title
Fan-Out Queue Saturation via Caller-Agnostic `VerifyForeignTransactionRequest` Key Blocks Foreign-Transaction Verification — (File: `crates/contract/src/lib.rs`)

---

### Summary

The `verify_foreign_transaction()` entry point constructs its pending-request map key (`VerifyForeignTransactionRequest`) without binding the caller's account ID, unlike `sign()` and `request_app_private_key()` which both embed `predecessor` in their keys. Because the fan-out queue for each key is shared across all callers, any unprivileged account can saturate the 128-slot cap for a specific foreign-transaction key, causing every subsequent `verify_foreign_transaction()` call for that same transaction to panic with `PendingRequestQueueFull` and permanently blocking bridge verification for that transaction until the attacker's yields drain.

---

### Finding Description

**Root cause — missing caller binding in the request key**

`sign()` and `request_app_private_key()` both embed the caller's identity in their request keys:

```rust
// lib.rs ~L379
let request = SignatureRequest::new(
    request.domain_id,
    request.payload,
    &predecessor,   // ← caller bound into key
    &request.path,
);
``` [1](#0-0) 

```rust
// lib.rs ~L493
let request = CKDRequest::new(
    request.app_public_key,
    domain_id,
    &predecessor,   // ← caller bound into key
    &request.derivation_path,
);
``` [2](#0-1) 

`verify_foreign_transaction()` does not:

```rust
// dto_mapping.rs ~L840
pub fn args_into_verify_foreign_tx_request(
    args: dtos::VerifyForeignTransactionRequestArgs,
) -> dtos::VerifyForeignTransactionRequest {
    dtos::VerifyForeignTransactionRequest {
        domain_id: args.domain_id,
        request: args.request,          // ← no predecessor
        payload_version: args.payload_version,
    }
}
``` [3](#0-2) 

The resulting `VerifyForeignTransactionRequest` struct contains only `{request, domain_id, payload_version}`:

```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
    // no caller field
}
``` [4](#0-3) 

**Fan-out cap enforcement**

`push_pending_yield` panics once the queue for a key reaches `MAX_PENDING_REQUEST_FAN_OUT = 128`:

```rust
pub const MAX_PENDING_REQUEST_FAN_OUT: u8 = 128;

pub(crate) fn push_pending_yield<K>(...) {
    let queue = requests.entry(request).or_default();
    if queue.len() >= usize::from(MAX_PENDING_REQUEST_FAN_OUT) {
        env::panic_str(
            &RequestError::PendingRequestQueueFull { limit: MAX_PENDING_REQUEST_FAN_OUT }
                .to_string(),
        );
    }
    queue.push(YieldIndex { data_id });
}
``` [5](#0-4) 

Because the key is caller-agnostic, all callers share the same 128-slot queue for a given `(ForeignChainRpcRequest, domain_id, payload_version)` triple.

**Attack path**

1. A bridge user submits a foreign-chain transaction (e.g., a Bitcoin tx with `tx_id = X`). The tx_id is publicly visible on-chain.
2. An attacker observes `tx_id = X` and front-runs the victim by calling `verify_foreign_transaction()` 128 times with the identical `VerifyForeignTransactionRequestArgs{request: Bitcoin(tx_id=X), domain_id, payload_version}`.
3. The fan-out queue for that key is now full (128 entries, all owned by the attacker).
4. The victim's `verify_foreign_transaction()` call panics: `PendingRequestQueueFull { limit: 128 }`.
5. The victim cannot get a signature for their bridge transaction.

**Persistence**

- If MPC nodes process the request and call `respond_verify_foreign_tx()`, `resolve_yields_for` drains the entire queue in one pass, resuming all 128 of the attacker's yields with the same signature. The attacker immediately re-fills the queue with 128 new calls.
- If MPC nodes do not respond, each yield times out after ~200 blocks. `pop_oldest_pending_yield` removes one entry per timeout (FIFO), so the queue drains one slot at a time — the victim must wait up to 128 × 200 = 25,600 blocks for the queue to fully drain. [6](#0-5) 

The attacker's cost is 128 × 1 yoctoNEAR deposit plus gas — negligible.

---

### Impact Explanation

A single unprivileged account can permanently block `verify_foreign_transaction()` for any specific foreign-chain transaction whose tx_id is known in advance (all bridge transactions). The victim's bridge flow is stalled: they cannot obtain the MPC signature needed to complete the cross-chain operation. This breaks the request-lifecycle safety invariant of the bridge without requiring any privileged access, threshold collusion, or network-level DoS.

---

### Likelihood Explanation

Foreign-chain transaction IDs are publicly observable before the victim calls `verify_foreign_transaction()`. The attacker only needs to monitor the foreign chain (or the NEAR mempool) and submit 128 cheap calls. The attack is repeatable after every drain cycle. Any party with a financial incentive to delay or block a specific bridge transaction (e.g., a competing bridge user, a MEV actor, or a protocol adversary) can execute this with minimal cost.

---

### Recommendation

Bind the caller's account ID into the `VerifyForeignTransactionRequest` key, mirroring the pattern used by `sign()` and `request_app_private_key()`. Concretely, include `predecessor_account_id` in `VerifyForeignTransactionRequest` (or in a wrapper key) so that each caller has an independent fan-out queue slot and cannot saturate the shared queue for a given foreign transaction.

---

### Proof of Concept

```
// Attacker script (pseudo-code):
let victim_tx_id = observe_foreign_chain(); // e.g., Bitcoin tx_id = X

let args = VerifyForeignTransactionRequestArgs {
    request: ForeignChainRpcRequest::Bitcoin(BitcoinRpcRequest { tx_id: victim_tx_id, ... }),
    domain_id: FOREIGN_TX_DOMAIN_ID,
    payload_version: ForeignTxPayloadVersion::V1,
};

// Fill the queue to MAX_PENDING_REQUEST_FAN_OUT = 128
for _ in 0..128 {
    near_call("verify_foreign_transaction", args, deposit=1_yoctoNEAR);
}

// Victim's call now panics:
// PendingRequestQueueFull { limit: 128 }
near_call("verify_foreign_transaction", args, deposit=1_yoctoNEAR); // ← PANICS
```

The `push_pending_yield` panic at `pending_requests.rs:52-57` is the exact revert point. The victim's yield is never created; their bridge transaction cannot proceed. [7](#0-6) [8](#0-7)

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

**File:** crates/contract/src/lib.rs (L493-498)
```rust
        let request = CKDRequest::new(
            request.app_public_key,
            domain_id,
            &predecessor,
            &request.derivation_path,
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

**File:** crates/contract/src/pending_requests.rs (L97-112)
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
}
```
