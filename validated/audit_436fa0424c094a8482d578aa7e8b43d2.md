### Title
Caller-Agnostic `verify_foreign_transaction` Request Key Enables Queue Saturation, Blocking Foreign-Chain Bridge Verification - (File: crates/contract/src/lib.rs)

### Summary
The `verify_foreign_transaction` function uses a caller-agnostic request key (`VerifyForeignTransactionRequest` contains no caller account ID). An unprivileged attacker can saturate the 128-slot fan-out queue for any specific foreign transaction at negligible cost, then continuously refill it, permanently blocking legitimate bridge services from submitting that same verification request. This is the direct analog of the "empty trades" pattern: the attacker submits requests that mutate contract state (queue entries) without serving the intended purpose (bridge verification), blocking legitimate operations.

### Finding Description

`verify_foreign_transaction` in `crates/contract/src/lib.rs` converts `VerifyForeignTransactionRequestArgs` to `VerifyForeignTransactionRequest` via `args_into_verify_foreign_tx_request`. The resulting key type is:

```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
``` [1](#0-0) 

No caller account ID is present. This is structurally different from `SignatureRequest`, whose map key includes a `tweak` derived from `predecessor_id` and `path`, making every caller's key unique:

```rust
pub fn new(domain: DomainId, payload: Payload, predecessor_id: &AccountId, path: &str) -> Self {
    let tweak = crate::kdf::derive_tweak(predecessor_id, path);
    ...
}
``` [2](#0-1) 

The fan-out queue is capped at `MAX_PENDING_REQUEST_FAN_OUT = 128`. Once full, any new submission panics with `PendingRequestQueueFull`: [3](#0-2) 

The test suite explicitly acknowledges the caller-agnostic key and the blocking consequence:

```
// And: caller bob submits the identical request — a different account would today
// be blocked from receiving a response by alice's submission.
``` [4](#0-3) 

Attack path:
1. Attacker monitors the foreign chain (e.g., Bitcoin) for a transaction a bridge is about to submit.
2. Attacker calls `verify_foreign_transaction` 128 times for that specific `tx_id` (cost: 128 yoctonear total).
3. Queue is saturated.
4. Bridge service's call fails with `PendingRequestQueueFull`.
5. MPC nodes respond, draining the queue.
6. Attacker immediately resubmits 128 requests.
7. Bridge is permanently blocked from processing that transaction. [5](#0-4) 

### Impact Explanation

A bridge service relying on `verify_foreign_transaction` to release funds on the NEAR side cannot process the targeted foreign transaction. If the attacker continuously refills the queue (cost remains ~128 yoctonear per cycle), the block is permanent. User funds on the foreign chain are frozen with no recourse until the attacker stops. This breaks the production safety invariant that any legitimate caller can always submit a foreign-chain verification request for a pending transaction.

### Likelihood Explanation

- **Attacker preconditions**: any NEAR account with 1 yoctonear per call; no privileged access required.
- **Knowledge required**: the foreign chain is public; the attacker can observe pending transactions before a bridge submits them.
- **Cost**: 128 yoctonear per saturation cycle — effectively free.
- **Timing**: the attacker can pre-fill the queue before the bridge submits, or refill it within the same block the MPC nodes drain it.

### Recommendation

Include the caller's `predecessor_account_id` in the `VerifyForeignTransactionRequest` key (analogous to how `SignatureRequest` embeds the caller via `tweak`). This ensures each caller occupies a distinct queue slot and an attacker cannot exhaust the quota for other callers.

Alternatively, enforce a per-`predecessor_account_id` submission rate limit inside `verify_foreign_transaction` before the yield is enqueued.

### Proof of Concept

```
1. Bridge service (alice.near) prepares to call verify_foreign_transaction
   for Bitcoin tx_id = [0xAB; 32], domain_id = 3.

2. Attacker (eve.near) submits 128 identical calls:
   verify_foreign_transaction({
     domain_id: 3,
     payload_version: V1,
     request: Bitcoin { tx_id: [0xAB;32], confirmations: 1, extractors: [BlockHash] }
   })
   with 1 yoctonear each → total cost 128 yoctonear.

3. pending_verify_foreign_tx_requests[key].len() == 128 (queue full).

4. alice.near calls verify_foreign_transaction with the same args →
   panics: "Pending-request queue is full for this request key (limit: 128)."

5. MPC nodes respond_verify_foreign_tx → queue drained.

6. Attacker immediately resubmits 128 calls → queue full again.

7. alice.near's retry fails again. User funds on Bitcoin remain frozen.
``` [6](#0-5) [7](#0-6)

### Citations

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L124-128)
```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
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

**File:** crates/contract/src/lib.rs (L3241-3253)
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
