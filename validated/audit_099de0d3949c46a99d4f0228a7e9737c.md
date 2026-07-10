### Title
Caller-Agnostic `verify_foreign_transaction` Queue Can Be Saturated by Unprivileged Attacker, Permanently Blocking Legitimate Requests - (File: crates/contract/src/pending_requests.rs)

### Summary

The `verify_foreign_transaction` fan-out queue uses a **caller-agnostic** request key. Any unprivileged account can fill the 128-slot queue for a specific foreign transaction by submitting 128 duplicate requests at negligible cost (128 yoctonear ≈ $0), permanently blocking any other account from submitting the same verification request as long as the attacker continuously refills the queue after each drain.

### Finding Description

The contract stores pending yield-resume promises in a `LookupMap<K, Vec<YieldIndex>>` keyed by the request type. For `sign()`, the key is a `SignatureRequest` that embeds a `tweak` derived from `(predecessor_account_id, path)`, making it caller-specific. For `verify_foreign_transaction()`, the key is a `VerifyForeignTransactionRequest` that contains only the foreign-chain details (chain, tx_id, domain_id, extractors) — **the caller's account ID is not part of the key**. [1](#0-0) 

The queue is capped at `MAX_PENDING_REQUEST_FAN_OUT = 128`. When the cap is reached, `push_pending_yield` calls `env::panic_str`, reverting the transaction: [2](#0-1) 

The test suite explicitly confirms the caller-agnostic key and the shared queue: [3](#0-2) 

The comment at line 3242–3243 reads: *"a different account would today be blocked from receiving a response by alice's submission"* — confirming that any account's submission occupies the shared queue slot.

Attack path:
1. Attacker submits 128 `verify_foreign_transaction` calls for the same `(chain, tx_id, domain_id)` tuple. Each costs 1 yoctonear.
2. Queue is saturated at `MAX_PENDING_REQUEST_FAN_OUT`.
3. Any legitimate user submitting the same request receives `PendingRequestQueueFull` panic.
4. MPC nodes eventually process the request and drain the queue via `respond_verify_foreign_tx`.
5. Attacker immediately resubmits 128 requests, re-saturating the queue before the legitimate user can submit.
6. The cycle repeats indefinitely at ~128 yoctonear per drain cycle.

There is no escape hatch: the queue can only be drained

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

**File:** crates/contract/src/lib.rs (L3209-3263)
```rust
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
