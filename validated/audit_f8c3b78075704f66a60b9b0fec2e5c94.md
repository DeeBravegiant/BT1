### Title
Caller-Agnostic `verify_foreign_transaction` Queue Key Allows Any Unprivileged Account to Saturate the Bounded Fan-Out Queue and Permanently Block Specific Foreign-Transaction Verifications - (File: `crates/contract/src/lib.rs`, `crates/contract/src/pending_requests.rs`)

---

### Summary

`verify_foreign_transaction` stores pending requests under a key that does **not** include the caller's account ID. Because the fan-out queue for each key is hard-capped at `MAX_PENDING_REQUEST_FAN_OUT = 128`, any unprivileged account can submit 128 identical requests for a known foreign-chain transaction (costing 128 yoctonear total) and permanently hold the queue at capacity. Every subsequent legitimate submission for that same transaction is rejected with `PendingRequestQueueFull`, and the attacker can immediately refill the queue after each MPC response drains it.

---

### Finding Description

**Root cause — caller-agnostic request key for `verify_foreign_transaction`**

`sign()` and `request_app_private_key()` both embed the caller's identity into the stored request key via a tweak derived from `predecessor_id + path`:

```rust
// crates/near-mpc-crypto-types/src/sign.rs:118-125
impl SignatureRequest {
    pub fn new(domain: DomainId, payload: Payload, predecessor_id: &AccountId, path: &str) -> Self {
        let tweak = crate::kdf::derive_tweak(predecessor_id, path);
        SignatureRequest { domain_id: domain, tweak, payload }
    }
}
``` [1](#0-0) 

`verify_foreign_transaction()` does the opposite — it converts the user-supplied args directly to a `VerifyForeignTransactionRequest` that contains **no caller field**:

```rust
// crates/near-mpc-contract-interface/src/types/foreign_chain.rs
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
    // ← no predecessor_id, no tweak, no caller identity
}
``` [2](#0-1) 

The contract stores this caller-agnostic struct as the map key:

```rust
// crates/contract/src/lib.rs:549-556
let request = args_into_verify_foreign_tx_request(request);
...
move |this, id| this.add_verify_foreign_tx_request(request, id),
``` [3](#0-2) 

**Bounded queue cap**

The fan-out queue for every request key is capped at 128 entries. Exceeding the cap panics with `PendingRequestQueueFull`:

```rust
// crates/contract/src/pending_requests.rs:37,51-57
pub const MAX_PENDING_REQUEST_FAN_OUT: u8 = 128;
...
if queue.len() >= usize::from(MAX_PENDING_REQUEST_FAN_OUT) {
    env::panic_str(&RequestError::PendingRequestQueueFull { limit: MAX_PENDING_REQUEST_FAN_OUT }.to_string());
}
``` [4](#0-3) 

**The codebase itself acknowledges the shared-queue design for `verify_foreign_transaction`**

The unit test comment at line 3242 explicitly notes: *"a different account would today be blocked from receiving a response by alice's submission"* — confirming that the queue is shared across all callers for the same foreign-tx request key. [5](#0-4) 

**Attack path**

1. Attacker observes (or predicts from the foreign chain's mempool) that a user will call `verify_foreign_transaction` for Bitcoin `tx_id = X`, `domain_id = D`, `payload_version = V1`.
2. Attacker submits 128 identical calls with the same `{tx_id, domain_id, payload_version}`, each attaching the minimum 1 yoctonear deposit. Total cost: 128 yoctonear ≈ $0.
3. Queue for key `{Bitcoin(X), D, V1}` is now at capacity.
4. Victim's call is rejected: `PendingRequestQueueFull`.
5. MPC nodes eventually respond, draining all 128 attacker yields. Attacker immediately resubmits 128 more.
6. Victim's retries are perpetually rejected.

---

### Impact Explanation

A specific foreign-chain transaction verification can be permanently blocked by a single unprivileged account at negligible cost. Any bridge or dApp that relies on `verify_foreign_transaction` to release or route funds tied to a known on-chain transaction ID is subject to indefinite DoS. The victim's yield-resume promise never resolves, and the bridge operation stalls. This breaks the request-lifecycle safety invariant of the foreign-chain verification flow without requiring any operator misconfiguration or network-level attack.

---

### Likelihood Explanation

Foreign-chain transaction IDs are public. An attacker monitoring the foreign chain (e.g., Bitcoin mempool or confirmed blocks) can predict or observe the exact `tx_id` a bridge user will submit before or immediately after the user's NEAR transaction lands. The attack requires no special privilege, no key material, and costs under 1 NEAR cent per saturation cycle. The attacker can sustain the attack indefinitely.

---

### Recommendation

Include the caller's account ID in the `VerifyForeignTransactionRequest` key, mirroring the design of `SignatureRequest`. Derive a per-caller tweak (or simply append `predecessor_account_id()`) so that each caller's submission occupies its own queue slot and cannot be saturated by a third party. Alternatively, enforce a per-account submission rate limit at the contract level for `verify_foreign_transaction`.

---

### Proof of Concept

The existing unit test `verify_foreign_transaction__should_queue_duplicates_from_different_callers` (lines 3208–3298 of `crates/contract/src/lib.rs`) already demonstrates that Alice and Bob share the same queue entry for identical requests. Extending this test to 128 submissions from a single attacker account before Alice's call demonstrates the `PendingRequestQueueFull` rejection:

```rust
// Attacker fills the queue
for _ in 0..128 {
    testing_env!(VMContextBuilder::new()
        .signer_account_id(attacker.clone())
        .predecessor_account_id(attacker.clone())
        .attached_deposit(NearToken::from_yoctonear(1))
        .build());
    contract.verify_foreign_transaction(request_args.clone()); // queues attacker yield
}

// Victim's call now panics with PendingRequestQueueFull
testing_env!(VMContextBuilder::new()
    .signer_account_id(victim.clone())
    .predecessor_account_id(victim.clone())
    .attached_deposit(NearToken::from_yoctonear(1))
    .build());
// → panics: "Pending-request queue is full for this request key (limit: 128)"
contract.verify_foreign_transaction(request_args.clone());
``` [6](#0-5) [7](#0-6) [2](#0-1) [8](#0-7)

### Citations

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

**File:** crates/contract/src/lib.rs (L3208-3263)
```rust
    #[test]
    fn verify_foreign_transaction__should_queue_duplicates_from_different_callers() {
        // Given: two different callers will submit the same foreign-tx verification request.
        let mut rng = rand::rngs::StdRng::from_seed([42u8; 32]);
        let (context, mut contract, secret_key) =
            basic_setup_with_protocol(Protocol::CaitSith, DomainPurpose::ForeignTx, &mut rng);
        register_supported_chains(&mut contract, [dtos::ForeignChain::Bitcoin]);
        let SharedSecretKey::Secp256k1(secret_key) = secret_key else {
            unreachable!();
        };

        let request_args = VerifyForeignTransactionRequestArgs {
            domain_id: DomainId::default().0.into(),
            payload_version: ForeignTxPayloadVersion::V1,
            request: dtos::ForeignChainRpcRequest::Bitcoin(BitcoinRpcRequest {
                tx_id: [7u8; 32].into(),
                confirmations: 2.into(),
                extractors: vec![BitcoinExtractor::BlockHash],
            }),
        };
        let request = args_into_verify_foreign_tx_request(request_args.clone());

        // When: caller alice submits the request.
        let alice = AccountId::from_str("alice.near").unwrap();
        testing_env!(
            VMContextBuilder::new()
                .signer_account_id(alice.clone())
                .predecessor_account_id(alice)
                .current_account_id(context.current_account_id.clone())
                .attached_deposit(NearToken::from_yoctonear(1))
                .build()
        );
        contract.verify_foreign_transaction(request_args.clone());

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
        assert_eq!(
            contract
                .pending_verify_foreign_tx_requests
                .get(&request)
                .map(|q| q.len()),
            Some(2),
            "duplicate foreign-tx requests from different callers should fan out",
        );
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
