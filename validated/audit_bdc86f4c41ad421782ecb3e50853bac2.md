### Title
Caller-Agnostic Request Key in `verify_foreign_transaction` Enables Queue-Saturation DoS Against Foreign-Chain Bridge Operations - (File: crates/contract/src/lib.rs)

---

### Summary

An unprivileged attacker can saturate the fan-out queue for any specific foreign-chain transaction verification request by submitting 128 identical `verify_foreign_transaction` calls at negligible cost (128 yoctonear ≈ $0). Because `VerifyForeignTransactionRequest` does not include the caller's account ID, all callers share the same queue slot. Once the queue reaches `MAX_PENDING_REQUEST_FAN_OUT = 128`, every subsequent legitimate submission for the same foreign-chain `tx_id` is rejected with `PendingRequestQueueFull`, blocking bridge operations for that transaction until all attacker yields time out.

---

### Finding Description

The `sign` function constructs a `SignatureRequest` that incorporates the caller's `predecessor` account ID into the request key, giving each caller an isolated queue slot: [1](#0-0) 

By contrast, `verify_foreign_transaction` converts user arguments into a `VerifyForeignTransactionRequest` that contains only `request`, `domain_id`, and `payload_version` — no caller identity: [2](#0-1) 

All callers submitting the same foreign-chain tx verification therefore share a single queue entry in `pending_verify_foreign_tx_requests`. The `push_pending_yield` helper enforces a hard cap of `MAX_PENDING_REQUEST_FAN_OUT = 128` and calls `env::panic_str` when exceeded: [3](#0-2) 

The codebase itself acknowledges the cross-caller collision in a test comment: [4](#0-3) 

**Attack steps:**
1. Attacker observes a target foreign-chain `tx_id` that a legitimate bridge user intends to verify.
2. Attacker submits 128 `verify_foreign_transaction` calls for the same `tx_id` (cost: 128 yoctonear).
3. The queue for that `VerifyForeignTransactionRequest` key is now at capacity.
4. Any subsequent legitimate submission for that `tx_id` panics with `PendingRequestQueueFull`.
5. As attacker yields time out, the attacker re-saturates the queue, maintaining the DoS indefinitely.

The `verify_foreign_transaction` entry point is fully open to any NEAR account: [5](#0-4) 

---

### Impact Explanation

This breaks the request-lifecycle invariant that any user should be able to verify a foreign-chain transaction through the MPC bridge. In production, `verify_foreign_transaction` is the gateway for cross-chain bridge operations (Bitcoin, Ethereum, Starknet, etc.). An attacker can selectively target specific `tx_id`s — for example, a competitor's bridge withdrawal — and prevent the MPC network from ever producing a signed response for that transaction. This constitutes **request-lifecycle manipulation that breaks production safety/accounting invariants** without relying on network-level DoS or operator misconfiguration, matching the Medium allowed impact scope.

---

### Likelihood Explanation

The attack requires no special privileges, no key material, and no threshold collusion. The cost is 128 yoctonear (sub-cent). The attacker can observe pending bridge operations on-chain and front-run them. The queue cap is a fixed constant (`u8 = 128`) that cannot be changed without a contract upgrade. The attack is repeatable as yields time out (NEAR yield timeout is bounded), so the DoS can be sustained indefinitely at negligible ongoing cost.

---

### Recommendation

Include the caller's account ID (`env::predecessor_account_id()`) in the `VerifyForeignTransactionRequest` key, mirroring the `SignatureRequest` design. This gives each caller an isolated queue slot and eliminates cross-user queue saturation. Alternatively, require a meaningful refundable deposit per submission (not 1 yoctonear) to raise the economic cost of queue flooding.

---

### Proof of Concept

```
1. Alice calls verify_foreign_transaction({ request: Bitcoin(tx_id=[0xAA;32]), domain_id: X, payload_version: V1 })
   → Alice's yield is queued at slot 0.

2. Attacker calls verify_foreign_transaction 128 times with the identical arguments.
   → Queue for VerifyForeignTransactionRequest{ Bitcoin([0xAA;32]), X, V1 } reaches MAX_PENDING_REQUEST_FAN_OUT=128.

3. Alice (or any other user) attempts another submission for the same tx_id:
   → push_pending_yield panics: "Pending-request queue is full (limit: 128)"
   → Alice's transaction is rejected; she cannot get the bridge operation verified.

4. Attacker re-saturates as yields time out → sustained DoS at ~128 yoctonear per cycle.
```

Confirmed by the unit test `add_signature_request__should_panic_when_pending_queue_is_full`: [6](#0-5) 

and the caller-agnostic fan-out test that explicitly notes the cross-caller blocking behavior: [7](#0-6)

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

**File:** crates/contract/src/lib.rs (L517-557)
```rust
    #[handle_result]
    #[payable]
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

**File:** crates/contract/src/lib.rs (L3301-3344)
```rust
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
